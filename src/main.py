# ============================================================
# SMART IRRIGATION SYSTEM API
# ============================================================
# Purpose: RESTful API for managing smart plant watering system
# with weather integration and moisture monitoring
# ============================================================

# Import required libraries for FastAPI framework
import json
import os

from fastapi import Body, FastAPI, Depends

# Import Pydantic for request/response validation
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Import type hints for better code clarity
from typing import List, Optional, Dict

# Import datetime utilities for timestamp handling and time calculations
from datetime import datetime, timedelta

# Import requests for external API calls (weather data)
import requests

# Import SQLAlchemy ORM components for database management
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from sklearn.ensemble import RandomForestClassifier
import numpy as np

from collections import defaultdict

valve_history = defaultdict(list)

watering_sessions = {}   # active watering (temporary)
lifetime_stats_store = {}  # permanent stats (never reset)

# ========================
# Meta file
############################


META_FILE = "bed_meta.json"
if os.path.exists(META_FILE):
    with open(META_FILE, "r") as f:
        bed_metadata = json.load(f)

# ============================================================
# 🧠 APP SETUP & DATABASE CONFIGURATION
# ============================================================g
global_weather = {"will_rain": False, "raw": None, "last_update": None}

active_valves = {}  # bed_id -> {"state": "ON/OFF", "until": datetime}

# Initialize FastAPI application with title and description

app = FastAPI(title="Smart Irrigation System")

# Define database URL (SQLite database stored locally)
DATABASE_URL = "sqlite:///./database.db"

# Create database engine with SQLite connection
# check_same_thread=False allows multi-threaded access to SQLite
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Create session factory for database operations
# autoflush=False: manual control over when changes are flushed
# autocommit=False: explicit transaction management required
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Create declarative base for ORM model definitions
Base = declarative_base()

# -----------------------------
# 🌧️ WEATHER CONFIG
# -----------------------------
OPENWEATHER_API_KEY = "e88c64c56baab21c5eeff4def1c026be"

# Location for weather queries (city, country)
CITY = "Detroit,US"

# In-memory cache to reduce API calls to OpenWeather service
# Stores last update timestamp and weather data to implement caching logic
weather_cache = {
    "last_update": None,  # Timestamp of last successful API call
    "data": None,  # Cached weather response data
}

# ============================================================
# 🌿 DATABASE MODELS (ORM)
# ============================================================
# BedMeta: Stores metadata for plant beds (name, icon) for display purposes
class BedMetaDB(Base):
    __tablename__ = "bed_meta"

    id = Column(Integer, primary_key=True)
    bed_id = Column(String, unique=True, index=True, nullable=False)

    name = Column(String, default="")
    icon = Column(String, default="🌱")

# BedReading: Records sensor data from a plant bed at specific timestamps
# Each reading captures all sensors' measurements and system state
class BedReading(Base):
    """
    ORM model for storing plant bed sensor readings.

    Represents a single snapshot of sensor data from a plant bed,
    including moisture levels, valve state, and signal strength.
    """

    __tablename__ = "bed_readings"

    # Unique identifier for the reading
    id = Column(Integer, primary_key=True)

    # Foreign key reference to plant bed (indexed for efficient queries)
    bed_id = Column(String, index=True)

    # Timestamp when sensor reading was captured (UTC)
    timestamp = Column(DateTime)

    # Average moisture level across all sensors
    average = Column(Float)

    # Current state of irrigation valve (e.g., "ON", "OFF", "COOLDOWN")
    valve_state = Column(String)


    #weather data at time of reading (optional, can be null if API call fails)
    weather = Column(JSON, nullable=True)

    # Wireless signal strength indicator (WiFi RSSI in dBm)
    rssi = Column(Integer)

    # JSON array of individual sensor readings (allows variable sensor count)
    sensors = Column(JSON)


# BedConfig: Stores configuration parameters for automated watering logic
# Each plant bed can have customized thresholds and timing settings
class BedConfigDB(Base):
    """
    ORM model for storing plant bed configuration settings.

    Manages configurable parameters for the automated irrigation system,
    such as moisture thresholds and valve timing settings.
    """

    __tablename__ = "bed_config"

    # Unique identifier for the configuration
    id = Column(Integer, primary_key=True)

    # Unique plant bed identifier (unique constraint ensures one config per bed)
    bed_id = Column(String, unique=True)

    # Moisture level threshold below which watering should be triggered (0-1023)
    moisture_threshold = Column(Integer, default=600)

    # Duration in seconds to keep irrigation valve open
    watering_duration_sec = Column(Integer, default=3)

    # Cooldown period in seconds before next watering cycle can begin
    cooldown_sec = Column(Integer, default=30)

    # Interval in seconds between consecutive sensor readings
    sampling_interval_sec = Column(Integer, default=10)


# Create all defined tables in the database (if they don't exist)
Base.metadata.create_all(bind=engine)


# -----------------------------
# 🐶 DB DEPENDENCY
# -----------------------------
def get_db():
    """
    Dependency function to provide database session to route handlers.

    Creates a new database session for each request, ensuring proper
    resource management with automatic cleanup in a finally block.

    Yields:
        Session: SQLAlchemy session for database operations
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        # Always close the session, even if an error occurs
        db.close()


# ============================================================
# 📡 REQUEST/RESPONSE DATA MODELS (PYDANTIC)
# ============================================================


# BedData: Schema for incoming sensor data from ESP32 microcontroller
class BedData(BaseModel):
    """
    Pydantic model for validating incoming sensor readings from ESP32.

    Defines the structure and types of data received from plant bed sensors,
    including moisture readings, valve state, and network signal strength.
    """

    # Unique identifier for the plant bed
    bed_id: str

    # ISO format timestamp of when reading was captured
    timestamp: str  # ISO format string (e.g., "2026-04-17T10:30:00")

    # Array of individual sensor readings (moisture values)
    sensors: List[float]
    

    # Calculated average moisture level across all sensors
    average: float

    # Current state of irrigation valve control
    valve_state: str

    # Optional: WiFi signal strength in dBm (typically -100 to -30)
    rssi: Optional[int] = None


# BedConfig: Schema for configurable watering parameters
class BedConfig(BaseModel):
    """
    Pydantic model for updating plant bed configuration settings.

    Allows partial updates to watering logic parameters.
    All fields are optional to support patch-style updates.
    """

    # Moisture threshold below which watering is triggered (optional update)
    moisture_threshold: Optional[int] = None

    # Duration to run irrigation valve (optional update)
    watering_duration_sec: Optional[int] = None

    # Cooldown period between watering cycles (optional update)
    cooldown_sec: Optional[int] = None

    # Interval between sensor readings (optional update)
    sampling_interval_sec: Optional[int] = None


# ============================================================
# 🌧️ WEATHER DATA RETRIEVAL & CACHING
# ============================================================


def get_weather():
    now = datetime.utcnow()

    if weather_cache["last_update"]:
        if now - weather_cache["last_update"] < timedelta(minutes=10):
            return weather_cache["data"]

    url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?q={CITY}&appid={OPENWEATHER_API_KEY}&units=metric"
    )

    r = requests.get(url)
    data = r.json()

    will_rain = any(item.get("pop", 0) > 0.5 for item in data.get("list", [])[:6])

    result = {"will_rain": bool(will_rain), "last_update": now.isoformat()}

    weather_cache["last_update"] = now
    weather_cache["data"] = result

    return result


# ============================================================
# 📡 SENSOR DATA INGESTION ENDPOINT
# ============================================================


@app.post("/api/bed-data")
def receive_data(data: BedData, db: Session = Depends(get_db)):
    """
    Accept and store sensor readings from ESP32 microcontroller.

    Receives moisture sensor data, valve state, and network metrics from
    a plant bed's ESP32 controller and persists it to the database for
    historical analysis and real-time monitoring.

    Args:
        data (BedData): Validated sensor reading payload containing:
            - bed_id: Identifier of the plant bed
            - timestamp: When the reading was captured
            - sensors: Array of moisture values
            - average: Mean moisture level
            - valve_state: Valve control state
            - rssi: WiFi signal strength (optional)
        db (Session): Database session from dependency injection

    Returns:
        dict: Status response indicating success or error details

    Example:
        POST /api/bed-data
        {
            "bed_id": "bed_001",
            "timestamp": "2026-04-17T10:30:00",
            "sensors": [500, 520, 510],
            "average": 510.0,
            "valve_state": "OFF",
            "rssi": -65
        }
    """
    try:
        # Create ORM object from validated request data
        weather = current_weather()

        reading = BedReading(
            bed_id=data.bed_id,
            timestamp=datetime.fromisoformat(data.timestamp),  # Parse ISO timestamp
            average=data.average,
            valve_state=data.valve_state,
            rssi=data.rssi,
            sensors=data.sensors,
            weather=weather  
        )

        # Add reading to session and commit to database
        db.add(reading)
        db.commit()

        # Return success status
        return {"status": "ok"}

    except Exception as e:
        # Return error details if insertion fails
        return {"status": "error", "message": str(e)}


# ============================================================
# 📊 RETRIEVE LATEST SENSOR STATE FOR ALL BEDS
# ============================================================


@app.get("/api/beds")
def get_beds(db: Session = Depends(get_db)):
    """
    Retrieve the latest sensor reading for each plant bed.

    Queries the database to get the most recent reading from each bed,
    providing a real-time snapshot of the entire irrigation system state.

    Args:
        db (Session): Database session from dependency injection

    Returns:
        dict: Mapping of bed_id to latest reading data

    Example response:
        {
            "bed_001": {
                "bed_id": "bed_001",
                "timestamp": "2026-04-17T10:30:00",
                "average": 510.0,
                "valve_state": "OFF",
                "rssi": -65,
                "sensors": [500, 520, 510]
            }
        }
    """
    # Dictionary to store latest reading per bed
    beds = {}

    # Query all readings ordered by timestamp (newest first)
    rows = db.query(BedReading).order_by(BedReading.timestamp.desc()).all()

    # Iterate through all readings and keep only the latest per bed
    for r in rows:
        # Only store reading if we haven't seen this bed_id yet
        # (since results are ordered by timestamp DESC, first occurrence is latest)
        if r.bed_id not in beds:
            beds[r.bed_id] = {
                "bed_id": r.bed_id,
                "timestamp": r.timestamp,
                "average": r.average,
                "valve_state": r.valve_state,
                "rssi": r.rssi,
                "sensors": r.sensors,
            }

    return beds


# ============================================================
# 🌿 RETRIEVE HISTORICAL SENSOR DATA
# ============================================================


@app.get("/api/beds/{bed_id}/history")
def history(bed_id: str, db: Session = Depends(get_db)):
    """
    Retrieve recent historical readings for a specific plant bed.

    Returns up to the last 100 readings for a bed, sorted chronologically
    in descending order (newest first). Useful for inspecting recent
    behavior and trends.

    Args:
        bed_id (str): Identifier of the plant bed to query
        db (Session): Database session from dependency injection

    Returns:
        list: Array of reading objects with timestamp, moisture, valve state, and sensors

    Example:
        GET /api/beds/bed_001/history
        [
            {
                "timestamp": "2026-04-17T10:30:00",
                "average": 510.0,
                "valve_state": "OFF",
                "sensors": [500, 520, 510]
            },
            ...
        ]
    """
    # Query up to 100 most recent readings for this bed
    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.desc())
        .limit(100)
        .all()
    )

    # Transform ORM objects to dictionaries for JSON response
    return [
        {
            "timestamp": r.timestamp,
            "average": r.average,
            "valve_state": r.valve_state,
            "sensors": r.sensors,
        }
        for r in rows
    ]


# ============================================================
# 📊 TIME RANGE QUERY (FOR ADVANCED ANALYSIS)
# ============================================================


@app.get("/api/beds/{bed_id}/range")
def get_range(
    bed_id: str, start: datetime, end: datetime, db: Session = Depends(get_db)
):
    """
    Retrieve sensor readings within a specific time range.

    Allows querying a bed's data between two timestamps, useful for
    generating reports, analyzing specific periods, or graphing data
    with custom time windows.

    Args:
        bed_id (str): Identifier of the plant bed to query
        start (datetime): Start of time range (inclusive, ISO format)
        end (datetime): End of time range (inclusive, ISO format)
        db (Session): Database session from dependency injection

    Returns:
        list: Readings within the time range, sorted chronologically ascending

    Example:
        GET /api/beds/bed_001/range?start=2026-04-16T00:00:00&end=2026-04-17T00:00:00
    """
    # Query all readings within the specified time range
    # Ordered ascending so earliest readings come first
    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .filter(BedReading.timestamp >= start)
        .filter(BedReading.timestamp <= end)
        .order_by(BedReading.timestamp.asc())
        .all()
    )

    # Return readings with key fields for analysis
    return [
        {
            "timestamp": r.timestamp,
            "average": r.average,
            "valve_state": r.valve_state,
        }
        for r in rows
    ]


# ============================================================
# 📈 OPTIMIZED GRAPH DATA ENDPOINT
# ============================================================


@app.get("/api/beds/{bed_id}/graph")
def graph_data(bed_id: str, limit: int = 200, db: Session = Depends(get_db)):
    """
    Retrieve sensor data formatted for frontend charting libraries.

    Returns readings in a denormalized format (arrays of timestamps, averages,
    and valve states) that's directly compatible with JavaScript charting
    libraries like Chart.js or Plotly.

    Args:
        bed_id (str): Identifier of the plant bed to query
        limit (int): Maximum number of recent readings to return (default: 200)
        db (Session): Database session from dependency injection

    Returns:
        dict: Contains separate arrays for timestamps, moisture averages, and valve states

    Example response:
        {
            "timestamps": ["2026-04-17T10:00:00", "2026-04-17T10:10:00", ...],
            "average": [500.0, 510.0, 520.0, ...],
            "valve": ["OFF", "OFF", "ON", ...]
        }
    """
    # Query the most recent readings up to the limit
    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.desc())
        .limit(limit)
        .all()
    )

    # Reverse to chronological order (earliest to latest)
    # This ensures charts display left-to-right in time progression
    rows.reverse()

    # Return data in denormalized format optimized for charting
    return {
        "timestamps": [r.timestamp for r in rows],
        "average": [r.average for r in rows],
        "valve": [r.valve_state for r in rows],
    }


# ============================================================
# 📊 STATISTICAL ANALYSIS ENDPOINT
# ============================================================


@app.get("/api/beds/{bed_id}/stats")
def stats(bed_id: str, db: Session = Depends(get_db)):
    """
    Calculate aggregated statistics for a plant bed's moisture data.

    Computes summary statistics (count, min, max, average, latest) across
    all historical readings to provide insights into soil conditions and
    watering patterns.

    Args:
        bed_id (str): Identifier of the plant bed to analyze
        db (Session): Database session from dependency injection

    Returns:
        dict: Contains count, min, max, avg, and last moisture readings

    Example response:
        {
            "count": 1250,
            "min": 350.0,
            "max": 980.0,
            "avg": 580.5,
            "last": 510.0
        }
    """
    # Query all readings for the specified bed
    rows = db.query(BedReading).filter(BedReading.bed_id == bed_id).all()

    # Return error if no data exists for this bed
    if not rows:
        return {"error": "no data"}

    # Extract all average moisture values
    vals = [r.average for r in rows]

    # Calculate and return statistical summary
    return {
        "count": len(vals),  # Total number of readings
        "min": min(vals),  # Driest reading
        "max": max(vals),  # Wettest reading
        "avg": sum(vals) / len(vals),  # Mean moisture level
        "last": vals[-1],  # Most recent reading
    }


# ============================================================
# ⚙️ CONFIGURATION MANAGEMENT - RETRIEVE SETTINGS
# ============================================================


@app.get("/api/config/{bed_id}")
def get_config(bed_id: str, db: Session = Depends(get_db)):
    """
    Retrieve current configuration settings for a plant bed.

    Returns the customized watering parameters for a bed. If no configuration
    exists, creates and returns default settings for future use.

    Args:
        bed_id (str): Identifier of the plant bed
        db (Session): Database session from dependency injection

    Returns:
        dict: Configuration parameters including thresholds and timings

    Example response:
        {
            "bed_id": "bed_001",
            "moisture_threshold": 450,
            "watering_duration_sec": 3,
            "cooldown_sec": 30,
            "sampling_interval_sec": 10
        }
    """
    # Query for existing configuration for this bed
    config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    # If no configuration exists, create one with default values
    if not config:
        config = BedConfigDB(bed_id=bed_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    # Return configuration as dictionary
    return {
        "bed_id": bed_id,
        "moisture_threshold": config.moisture_threshold,
        "watering_duration_sec": config.watering_duration_sec,
        "cooldown_sec": config.cooldown_sec,
        "sampling_interval_sec": config.sampling_interval_sec,
    }


# ============================================================
# ⚙️ CONFIGURATION MANAGEMENT - UPDATE SETTINGS
# ============================================================


@app.post("/api/config/{bed_id}")
def update_config(bed_id: str, config: BedConfig, db: Session = Depends(get_db)):
    """
    Update configuration settings for a plant bed.

    Allows partial updates to watering logic parameters. Only provided
    fields are updated, leaving others unchanged. Automatically creates
    a new configuration if one doesn't exist.

    Args:
        bed_id (str): Identifier of the plant bed
        config (BedConfig): Configuration updates (all fields optional)
        db (Session): Database session from dependency injection

    Returns:
        dict: Status confirmation

    Example request:
        POST /api/config/bed_001
        {
            "moisture_threshold": 500,
            "watering_duration_sec": 5
        }
    """
    # Query for existing configuration or initialize new one
    db_config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    if not db_config:
        # Create new configuration if it doesn't exist
        db_config = BedConfigDB(bed_id=bed_id)
        db.add(db_config)

    # Update only the fields that were explicitly provided (not None)
    # exclude_unset=True ensures only provided fields are included
    for k, v in config.model_dump(exclude_unset=True).items():
        setattr(db_config, k, v)

    # Persist changes to database
    db.commit()

    return {"status": "updated"}


# ============================================================
# 🌧️ INTELLIGENT WATERING DECISION ENGINE
# ============================================================
@app.post("/api/should-water")
def should_water(bed_id: str, average_moisture: float, db: Session = Depends(get_db)):

    config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    if not config:
        config = BedConfigDB(bed_id=bed_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    soil_dry = average_moisture > config.moisture_threshold
    weather = current_weather()
    rain_expected = weather["is_raining_now"]

    water = soil_dry and not rain_expected

    now = datetime.utcnow()

    # 💧 THIS is the missing piece
    if water:
        active_valves[bed_id] = {
            "state": "ON",
            "until": now + timedelta(seconds=config.watering_duration_sec),
        }
    else:
        # optional safety turn-off logic
        if bed_id not in active_valves:
            active_valves[bed_id] = {"state": "OFF", "until": now}

    return {
        "bed_id": bed_id,
        "water": water,
        "soil_dry": soil_dry,
        "rain_expected": rain_expected,
        "weather": weather,
        "valve_state": active_valves.get(bed_id, {}).get("state", "OFF"),
    }


# ============================================================
# 🧪 SYSTEM HEALTH & MAINTENANCE ENDPOINTS
# ============================================================


@app.get("/health")
def health():
    """
    Health check endpoint for monitoring service availability.

    Simple endpoint used by load balancers and monitoring tools to verify
    that the API is running and responding to requests.

    Returns:
        dict: Status indicator

    Example:
        GET /health
        {"status": "alive"}
    """
    return {"status": "alive"}


@app.delete("/api/cleanup")
def cleanup(db: Session = Depends(get_db)):
    """
    Delete historical data older than 7 days.

    Maintenance endpoint to prevent database from growing unbounded.
    Removes readings older than one week to manage storage while keeping
    recent data for analysis and monitoring.

    Args:
        db (Session): Database session from dependency injection

    Returns:
        dict: Status confirmation

    Note:
        Should be called periodically (e.g., via a cron job or scheduler)
        to maintain optimal database performance.
    """
    # Calculate cutoff timestamp (7 days ago)
    cutoff = datetime.utcnow() - timedelta(days=7)

    # Delete all readings before the cutoff date
    db.query(BedReading).filter(BedReading.timestamp < cutoff).delete()
    db.commit()

    return {"status": "cleaned"}


@app.get("/api/beds/latest")
def latest(db: Session = Depends(get_db)):

    subquery = db.query(BedReading).order_by(BedReading.timestamp.desc()).all()
    seen = {}
    now = datetime.utcnow()

    for r in subquery:
        if r.bed_id not in seen:

            live = active_valves.get(r.bed_id)

            if live and now <= live["until"]:
                valve_state = "ON"
            else:
                # fallback to DB state instead of forcing OFF
                valve_state = r.valve_state

            seen[r.bed_id] = {
                "bed_id": r.bed_id,
                "average": r.average,
                "valve_state": valve_state,
                "timestamp": r.timestamp,
            }

    return seen

#################################
# main entry point for running the API server
###################################
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
<title>🌱 Smart Garden Control Panel</title>

<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">

<style>
body {
    background:#0f1115;
    color:white;
}

.card {
    background:#1b1f2a;
    border:1px solid #2a2f3a;
    transition: all 0.2s ease;
}

.navbar {
    background:black;
    border-bottom:1px solid #2a2f3a;
}

.status-good { color:#00ff9a; font-weight:bold; }
.status-warn { color:#ffcc00; font-weight:bold; }
.status-bad  { color:#ff4d4d; font-weight:bold; }

.small {
    font-size:12px;
    color:#9aa4b2;
}

/* 🌿 clickable cards */
.clickable-card {
    cursor: pointer;
}

.clickable-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 0 15px rgba(0,255,154,0.15);
    border-color: #00ff9a;
}

</style>
</head>

<body>

<nav class="navbar navbar-expand-lg navbar-dark">
  <div class="container-fluid">

    <a class="navbar-brand" href="/">🌱 Smart Garden</a>

    <div class="navbar-nav">
      <a class="nav-link" href="/">Dashboard</a>
      <a class="nav-link" href="/health-dashboard">🌿 Intelligence</a>
      <a class="nav-link" href="/about">About</a>
    </div>

  </div>
</nav>

<div class="container py-4">

<h2 class="mb-3">🌿 Garden Control Panel</h2>

<div id="weather" class="alert alert-info">Loading weather...</div>

<div class="row" id="beds"></div>

</div>

<footer style="text-align:center; padding:20px; color:#9aa4b2; border-top:1px solid #2a2f3a; margin-top:40px;">
    Made with 💖 Nicky Blackburn
</footer>

<script>

/* =========================
   STATE
========================= */
let bedMeta = {};

/* =========================
   LOAD META FROM SERVER
========================= */
async function loadMeta() {
    const res = await fetch("/api/beds/meta");
    bedMeta = await res.json();
}

/* =========================
   SAVE META TO SERVER
========================= */
async function saveMeta(bedId, meta) {
    await fetch(`/api/beds/${bedId}/meta`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(meta)
    });
}

/* =========================
   WEATHER
========================= */
async function loadWeather() {
    const res = await fetch('/api/weather');
    const data = await res.json();

    document.getElementById("weather").innerText =
        data.will_rain ? "🌧 Rain expected soon" : "☀ Stable conditions";
}

/* =========================
   STATUS
========================= */
function getStatus(avg) {
    if (avg > 700) return { text:"DRY", cls:"status-bad" };
    if (avg < 300) return { text:"WET", cls:"status-warn" };
    return { text:"HEALTHY", cls:"status-good" };
}

/* =========================
   NAVIGATION
========================= */
function goToBed(bedId) {
    window.location.href = `/health-dashboard?bed=${bedId}`;
}

/* =========================
   EDIT BED (stop navigation conflict)
========================= */
async function editBed(bedId) {

    const current = bedMeta[bedId] || {};

    const name = prompt("Plant / Bed Name:", current.name || bedId);
    if (name === null) return;

    const icon = prompt("Emoji / Icon:", current.icon || "🌱");
    if (icon === null) return;

    const meta = { name, icon };

    bedMeta[bedId] = meta;
    await saveMeta(bedId, meta);

    loadBeds();
}

/* =========================
   LOAD BEDS
========================= */
async function loadBeds() {

    const latest = await fetch('/api/beds/latest').then(r => r.json());

    let html = "";

    for (const bed in latest) {

        const b = latest[bed];
        const life = await fetch(`/api/beds/${bed}/lifetime`).then(r => r.json());

        const status = getStatus(b.average);

        const meta = bedMeta[b.bed_id] || {};
        const name = meta.name || b.bed_id;
        const icon = meta.icon || "🌱";

        html += `
        <div class="col-md-4 mb-3">

            <div class="card p-3 clickable-card"
                 onclick="goToBed('${b.bed_id}')">

                <h5>${icon} ${name}</h5>

                <div class="small">ID: ${b.bed_id}</div>

                <button class="btn btn-sm btn-outline-light mt-2"
                        onclick="event.stopPropagation(); editBed('${b.bed_id}')">
                    ✏ Edit
                </button>

                <p class="${status.cls} mt-2">${status.text}</p>

                <p>💧 Moisture: ${b.average.toFixed(1)}</p>
                <p>🚰 Valve: ${b.valve_state}</p>

                <hr>

                <p>🌊 Water Cycles: <b>${life.times_watered || 0}</b></p>
                <p>⏱ Total Watering: <b>${life.total_watering_minutes || 0} min</b></p>

                <p class="small">
                    Last watered: ${
                        life.last_watered
                            ? new Date(life.last_watered).toLocaleString()
                            : "Never"
                    }
                </p>

            </div>
        </div>
        `;
    }

    document.getElementById("beds").innerHTML = html;
}

/* =========================
   INIT
========================= */
(async function init() {
    await loadMeta();
    await loadBeds();
    await loadWeather();

    setInterval(loadBeds, 3000);
    setInterval(loadWeather, 10000);
})();

</script>

</body>
</html>
"""
#=======================================================
# 📖 ABOUT PAGE
# ============================================================


@app.get("/about", response_class=HTMLResponse)
def about_page():
    return """
<!DOCTYPE html>
<html>
<style>
    body {
        background: #0f1115;
        color: #ffffff;
    }

    h1, h2, h3, h4, h5 {
        color: #ffffff !important;
    }

    p, li, pre {
        color: #eaeaea;
    }

    .card {
        background: #1b1f2a;
        border: 1px solid #2a2f3a;
        color: #ffffff;
    }

    .text-muted {
        color: #b5b5b5 !important;
    }

    .tag {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        background: #2a2f3a;
        margin: 2px;
        font-size: 12px;
        color: #ffffff;
    }

    .hero {
        padding: 40px 0;
        text-align: center;
    }

    .glow {
        color: #00ff9a;
        text-shadow: 0 0 10px rgba(0,255,154,0.4);
    }
    h4 {
    color: #00ff9a !important;
    text-shadow: 0 0 6px rgba(0,255,154,0.25);
}
</style>
<head>
    <title>About · Smart Garden</title>

    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">

    <style>
        body {
            background: #0f1115;
            color: #e6e6e6;
        }

        .card {
            background: #1b1f2a;
            border: 1px solid #2a2f3a;
        }

        .tag {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            background: #2a2f3a;
            margin: 2px;
            font-size: 12px;
        }

        .hero {
            padding: 40px 0;
            text-align: center;
        }

        .glow {
            color: #00ff9a;
            text-shadow: 0 0 10px rgba(0,255,154,0.4);
        }
    </style>
</head>

<nav class="navbar navbar-expand-lg navbar-dark bg-black border-bottom border-secondary">
  <div class="container-fluid">

    <a class="navbar-brand" href="/">🌱 Smart Garden</a>

    <div class="navbar-nav">
      <a class="nav-link" href="/">Dashboard</a>
      <a class="nav-link" href="/health-dashboard">🌿 Intelligence</a>
      <a class="nav-link" href="/about">About</a>
    </div>

    </nav>
<body>

<div class="container py-5">

    <div class="hero">
        <h1 class="glow">🌱 Smart Garden System</h1>
        <p style="color: #ffffff;">IoT irrigation system with weather-aware automation</p>
    </div>

    <!-- ABOUT -->
    <div class="card p-4 mb-4">
        <h4>📌 Project Overview</h4>
        <p>
            This system is a smart irrigation network using FastAPI,
            Bed Modules (esp32 controlled Auto watering systems), and a real-time dashboard.
            It models how an ESP32-based garden would monitor soil moisture
            and automatically control watering based on environmental conditions.
        </p>
    </div>

    <!-- TECH STACK -->
    <div class="card p-4 mb-4">
        <h4>⚙️ Tech Stack</h4>

        <span class="tag">FastAPI</span>
        <span class="tag">SQLite</span>
        <span class="tag">SQLAlchemy</span>
        <span class="tag">Chart.js</span>
        <span class="tag">Bootstrap</span>
        <span class="tag">Bed Modules (ESP32) based</span>
        <span class="tag">OpenWeather API</span>
    </div>

    <!-- FEATURES -->
    <div class="card p-4 mb-4">
        <h4>🌿 Features</h4>

        <ul>
            <li>Real-time soil moisture monitoring</li>
            <li>Automatic watering decision engine</li>
            <li>Weather-aware irrigation logic</li>
            <li>Historical sensor data storage</li>
            <li>Graph-based moisture tracking</li>
            <li>Configurable watering thresholds</li>
        </ul>
    </div>

    <!-- CREATOR -->
    <div class="card p-4 mb-4">
        <h4>👤 Creator</h4>

        <p>
            Built by <b>Nicky Blackburn</b><br>
            A personal IoT + backend systems project exploring automation,
            sensors, and real-time data systems.
        </p>

        <p class="text-muted">
            Version: 1.0 · Prototype System
        </p>
    </div>

    <!-- SYSTEM ARCHITECTURE -->
    <div class="card p-4 mb-4">
        <h4>🧠 System Flow</h4>

        <pre style="color:#9aa4b2;">
ESP32 Bed Modules (soil moisture & control valves)
    ↓
FastAPI Server
    ↓
SQLite Database
    ↓
Decision Engine (watering logic)
    ↓
Dashboard UI (Chart.js)
        </pre>
    </div>

    <!-- NAV -->
    <div class="text-center mt-4">
        <a href="/" class="btn btn-outline-light">← Back to Dashboard</a>
    </div>

</div>

</body>
</html>
"""

#########################################
# Per bed Display of latest reading and valve state
#########################################
@app.get("/health-dashboard", response_class=HTMLResponse)
def garden_health_page():
    return """
<!DOCTYPE html>
<html>
<head>
<title>🌿 Garden Intelligence</title>

<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
body {
    background:#0f1115;
    color:white;
}

.card {
    background:#1b1f2a;
    border:1px solid #2a2f3a;
}

select {
    background:#1b1f2a !important;
    color:white !important;
    border:1px solid #2a2f3a !important;
}
</style>
</head>

<body>

<nav class="navbar navbar-expand-lg navbar-dark bg-black border-bottom border-secondary">
  <div class="container-fluid">

    <a class="navbar-brand" href="/">🌱 Smart Garden</a>

    <div class="navbar-nav">
      <a class="nav-link" href="/">Dashboard</a>
      <a class="nav-link" href="/health-dashboard">🌿 Intelligence</a>
      <a class="nav-link" href="/about">About</a>
    </div>

</nav>

<div class="container py-4">

<h1 class="mb-4">🌿 Garden Intelligence</h1>

<select id="bedSelect" class="form-select mb-3"></select>

<div class="card p-3 mb-3">
    <div id="liveStatus">Loading...</div>
</div>

<div class="card p-3">
    <canvas id="chart"></canvas>
</div>

</div>

<footer style="text-align:center; padding:20px; color:#9aa4b2; border-top:1px solid #2a2f3a; margin-top:40px;">
    <div style="margin-bottom:10px;">
        Made with 💖 Nicky Blackburn
    </div>

    <a href="/about" class="btn btn-outline-light btn-sm">
        About This Project
    </a>
</footer>

<script>

let chart = null;
let currentBed = null;

/* =========================
   GET URL PARAM
========================= */
function getBedFromURL() {
    const params = new URLSearchParams(window.location.search);
    return params.get("bed");
}

/* =========================
   CREATE CHART
========================= */
function createChart(data) {
    const ctx = document.getElementById("chart");

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data?.timestamps || [],
            datasets: [
                {
                    label: 'Soil Moisture',
                    data: data?.moisture || [],
                    borderWidth: 2,
                    tension: 0.3,
                    yAxisID: 'y'
                },
                {
                    label: 'Valve State',
                    data: data?.valve || [],
                    stepped: true,
                    borderWidth: 2,
                    yAxisID: 'y2'
                }
            ]
        },
        options: {
            animation: false,
            responsive: true,
            scales: {
                y: {
                    position: 'left',
                    title: { display: true, text: 'Moisture' }
                },
                y2: {
                    position: 'right',
                    min: 0,
                    max: 1,
                    grid: { drawOnChartArea: false }
                }
            }
        }
    });
}

/* =========================
   UPDATE CHART
========================= */
function updateChart(data) {
    if (!chart || !data) return;

    chart.data.labels = data.timestamps || [];
    chart.data.datasets[0].data = data.moisture || [];
    chart.data.datasets[1].data = data.valve || [];

    chart.update();
}

/* =========================
   LOAD GRAPH
========================= */
async function loadGraph(bed) {
    const res = await fetch(`/api/beds/${bed}/full-graph`);
    const data = await res.json();

    if (!chart) createChart(data);
    else updateChart(data);
}

/* =========================
   LIVE STATUS
========================= */
async function loadStatus() {
    if (!currentBed) return;

    const res = await fetch('/api/beds/latest');
    const data = await res.json();

    const b = data[currentBed];
    if (!b) return;

    let status = "🟢 Healthy";

    if (b.average > 700) status = "🔴 Dry";
    else if (b.average < 300) status = "🔵 Too Wet";

    document.getElementById("liveStatus").innerHTML = `
        <h5>${currentBed}</h5>
        <p>💧 Moisture: ${b.average.toFixed(1)}</p>
        <p>🚰 Valve: ${b.valve_state}</p>
        <p>${status}</p>
    `;
}

/* =========================
   LOAD BEDS (✨ FIXED)
========================= */
async function loadBeds() {
    const res = await fetch('/api/beds');
    const beds = await res.json();

    const select = document.getElementById("bedSelect");
    select.innerHTML = "";

    const selectedFromURL = getBedFromURL();

    for (const bed in beds) {
        const opt = document.createElement("option");
        opt.value = bed;
        opt.text = bed;
        select.appendChild(opt);
    }

    // ✨ Select correct bed
    if (selectedFromURL && beds[selectedFromURL]) {
        currentBed = selectedFromURL;
        select.value = selectedFromURL;
    } else {
        currentBed = select.value;
    }

    loadGraph(currentBed);
    loadStatus();

    select.addEventListener("change", () => {
        currentBed = select.value;

        // ✨ Update URL dynamically
        window.history.replaceState(null, "", `?bed=${currentBed}`);

        loadGraph(currentBed);
        loadStatus();
    });
}

/* =========================
   LIVE LOOP
========================= */
function startLive() {
    setInterval(() => {
        if (currentBed) {
            loadGraph(currentBed);
            loadStatus();
        }
    }, 3000);
}

/* =========================
   INIT
========================= */
loadBeds();
startLive();

</script>

</body>
</html>
"""
@app.get("/api/will-rain")
def weather_api():
    return get_weather()


@app.get("/api/weather/current")
def current_weather():
    """
    Returns real-time weather conditions (NOT forecast)
    """

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={CITY}&appid={OPENWEATHER_API_KEY}&units=metric"
    )

    r = requests.get(url)
    data = r.json()

    weather_main = data["weather"][0]["main"]  # Clear / Rain / Clouds
    description = data["weather"][0]["description"]

    is_raining_now = weather_main.lower() == "rain"

    return {
        "current": weather_main,
        "is_raining_now": is_raining_now,
        "temp": data["main"]["temp"],
        "humidity": data["main"]["humidity"],
    }


@app.post("/api/water")
def water_bed(bed_id: str, duration: int = 3):
    """
    Turns valve ON for a fixed duration (simulation of irrigation)
    """

    now = datetime.utcnow()
    active_valves[bed_id] = {"state": "ON", "until": now + timedelta(seconds=duration)}

    return {"bed_id": bed_id, "valve_state": "ON", "duration": duration}


@app.get("/api/valve/{bed_id}")
def valve_status(bed_id: str):
    now = datetime.utcnow()

    v = active_valves.get(bed_id)

    if not v:
        return {"bed_id": bed_id, "valve_state": "OFF"}

    if now > v["until"]:
        active_valves.pop(bed_id, None)
        return {"bed_id": bed_id, "valve_state": "OFF"}

    return {"bed_id": bed_id, "valve_state": "ON"}

    ############################################
    #Power modes endpoints
    ################################

@app.post("/api/beds/{bed_id}/mode")
def set_mode(bed_id: str, mode: str):
    active_valves.setdefault(bed_id, {})

    active_valves[bed_id]["mode"] = mode

    return {
        "bed_id": bed_id,
        "mode": mode
    }

@app.get("/api/beds/{bed_id}/mode")
def get_mode(bed_id: str):
    return {
        "bed_id": bed_id,
        "mode": active_valves.get(bed_id, {}).get("mode", "normal")
    }

@app.get("/api/beds/{bed_id}/full-graph")
def full_graph(bed_id: str, limit: int = 200, db: Session = Depends(get_db)):

    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.desc())
        .limit(limit)
        .all()
    )

    rows.reverse()

    timestamps = []
    moisture = []
    valve = []

    for r in rows:
        timestamps.append(r.timestamp.isoformat() if r.timestamp else "")
        moisture.append(r.average or 0)

        # 🔥 IMPORTANT FIX:
        # override DB with live valve if it exists
        live = active_valves.get(bed_id)

        if live:
            valve_state = 1 if live["state"] == "ON" else 0
        else:
            valve_state = 1 if r.valve_state == "ON" else 0

        valve.append(valve_state)

    return {
        "timestamps": timestamps,
        "moisture": moisture,
        "rain": [0] * len(timestamps),
        "valve": valve
    }



@app.get("/api/beds/{bed_id}/lifetime")
def lifetime_stats(bed_id: str, db: Session = Depends(get_db)):

    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.asc())
        .all()
    )

    if not rows:
        return {"error": "no data"}

    water_events = 0
    last_state = "OFF"
    last_watered = None
    total_on_time = timedelta(0)

    last_on_time = None

    for r in rows:

        # detect ON transition
        if r.valve_state == "ON" and last_state != "ON":
            water_events += 1
            last_on_time = r.timestamp
            last_watered = r.timestamp

        # detect OFF transition
        if r.valve_state == "OFF" and last_state == "ON":
            if last_on_time:
                total_on_time += (r.timestamp - last_on_time)
                last_on_time = None

        last_state = r.valve_state

    return {
        "bed_id": bed_id,
        "times_watered": water_events,
        "last_watered": last_watered,
        "total_watering_minutes": round(total_on_time.total_seconds() / 60, 2),
        "avg_moisture": sum(r.average for r in rows) / len(rows)
    }


from datetime import datetime

@app.post("/api/beds/{bed_id}/water-cycle")
def water_cycle(bed_id: str, valve_state: str):

    now = datetime.utcnow()

    # -------------------------
    # INIT STORAGE
    # -------------------------
    if bed_id not in lifetime_stats_store:
        lifetime_stats_store[bed_id] = {
            "times_watered": 0,
            "total_seconds": 0
        }

    if bed_id not in watering_sessions:
        watering_sessions[bed_id] = None

    # -------------------------
    # 🟢 START WATERING
    # -------------------------
    if valve_state == "ON":

        # only start if not already running
        if watering_sessions[bed_id] is None:
            watering_sessions[bed_id] = {
                "start": now
            }

        return {
            "bed_id": bed_id,
            "state": "started"
        }

    # -------------------------
    # 🔴 STOP WATERING
    # -------------------------
    if valve_state == "OFF":

        session = watering_sessions.get(bed_id)

        # only count if session exists
        if session is not None:

            duration = (now - session["start"]).total_seconds()

            lifetime_stats_store[bed_id]["times_watered"] += 1
            lifetime_stats_store[bed_id]["total_seconds"] += duration

            watering_sessions[bed_id] = None

            return {
                "bed_id": bed_id,
                "state": "stopped",
                "duration_sec": duration
            }

        # OFF but no session = ignore safely
        return {
            "bed_id": bed_id,
            "state": "ignored_no_session"
        }

    return {
        "bed_id": bed_id,
        "state": "no_change"
    }

@app.get("/api/beds/{bed_id}/lifetime")
def lifetime_stats_endpoint(bed_id: str):

    stats = lifetime_stats.get(bed_id, {
        "times_watered": 0,
        "total_seconds": 0
    })

    return {
        "bed_id": bed_id,
        "times_watered": stats["times_watered"],
        "total_watering_minutes": round(stats["total_seconds"] / 60, 2)
    }
@app.post("/api/beds/{bed_id}/meta")
def save_bed_meta(
    bed_id: str,
    data: dict = Body(...),
    db: Session = Depends(get_db)
):

    row = db.query(BedMetaDB).filter(BedMetaDB.bed_id == bed_id).first()

    if not row:
        row = BedMetaDB(bed_id=bed_id)
        db.add(row)

    row.name = data.get("name", bed_id)
    row.icon = data.get("icon", "🌱")

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "bed_id": bed_id,
        "meta": {
            "name": row.name,
            "icon": row.icon
        }
    }

@app.get("/api/beds/{bed_id}/meta")
def get_bed_meta(bed_id: str, db: Session = Depends(get_db)):
    row = db.query(BedMetaDB).filter(BedMetaDB.bed_id == bed_id).first()

    if not row:
        return {
            "bed_id": bed_id,
            "name": bed_id,
            "icon": "🌱"
        }

    return {
        "bed_id": bed_id,
        "name": row.name,
        "icon": row.icon
    }

@app.get("/api/beds/meta")
def get_all_bed_meta(db: Session = Depends(get_db)):
    rows = db.query(BedMetaDB).all()

    return {
        r.bed_id: {
            "name": r.name,
            "icon": r.icon
        }
        for r in rows
    }