from fastapi import FastAPI, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import requests

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# -----------------------------
# 🧠 APP SETUP
# -----------------------------
app = FastAPI(title="Smart Irrigation System")

DATABASE_URL = "sqlite:///./database.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# -----------------------------
# 🌧️ WEATHER CONFIG
# -----------------------------
OPENWEATHER_API_KEY = "YOUR_API_KEY"
CITY = "Troy,US"

weather_cache = {
    "last_update": None,
    "data": None
}

# -----------------------------
# 🌿 DATABASE TABLES
# -----------------------------
class BedReading(Base):
    __tablename__ = "bed_readings"

    id = Column(Integer, primary_key=True)
    bed_id = Column(String, index=True)

    timestamp = Column(DateTime)
    average = Column(Float)
    valve_state = Column(String)
    rssi = Column(Integer)
    sensors = Column(JSON)


class BedConfigDB(Base):
    __tablename__ = "bed_config"

    id = Column(Integer, primary_key=True)
    bed_id = Column(String, unique=True)

    moisture_threshold = Column(Integer, default=450)
    watering_duration_sec = Column(Integer, default=3)
    cooldown_sec = Column(Integer, default=30)
    sampling_interval_sec = Column(Integer, default=10)


Base.metadata.create_all(bind=engine)

# -----------------------------
# 🐶 DB DEPENDENCY
# -----------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------------
# 📡 ESP32 MODEL
# -----------------------------
class BedData(BaseModel):
    bed_id: str
    timestamp: datetime
    sensors: List[int]
    average: float
    valve_state: str
    rssi: Optional[int] = None


class BedConfig(BaseModel):
    moisture_threshold: Optional[int] = None
    watering_duration_sec: Optional[int] = None
    cooldown_sec: Optional[int] = None
    sampling_interval_sec: Optional[int] = None

# -----------------------------
# 🌧️ WEATHER FUNCTION
# -----------------------------
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

    will_rain = False

    for item in data.get("list", [])[:6]:
        if item.get("pop", 0) > 0.5:
            will_rain = True
            break

    result = {
        "will_rain": will_rain,
        "raw": data
    }

    weather_cache["last_update"] = now
    weather_cache["data"] = result

    return result

# -----------------------------
# 📡 INGEST DATA
# -----------------------------
@app.post("/api/bed-data")
def receive_data(data: BedData, db: Session = Depends(get_db)):

    reading = BedReading(
        bed_id=data.bed_id,
        timestamp=data.timestamp,
        average=data.average,
        valve_state=data.valve_state,
        rssi=data.rssi,
        sensors=data.sensors
    )

    db.add(reading)
    db.commit()

    return {"status": "ok"}

# -----------------------------
# 📊 LATEST STATE
# -----------------------------
@app.get("/api/beds")
def get_beds(db: Session = Depends(get_db)):

    beds = {}
    rows = db.query(BedReading).order_by(BedReading.timestamp.desc()).all()

    for r in rows:
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

# -----------------------------
# 🌿 HISTORY
# -----------------------------
@app.get("/api/beds/{bed_id}/history")
def history(bed_id: str, db: Session = Depends(get_db)):

    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.desc())
        .limit(100)
        .all()
    )

    return [
        {
            "timestamp": r.timestamp,
            "average": r.average,
            "valve_state": r.valve_state,
            "sensors": r.sensors
        }
        for r in rows
    ]

# -----------------------------
# 📊 RANGE QUERY (FOR GRAPHS)
# -----------------------------
@app.get("/api/beds/{bed_id}/range")
def get_range(
    bed_id: str,
    start: datetime,
    end: datetime,
    db: Session = Depends(get_db)
):

    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .filter(BedReading.timestamp >= start)
        .filter(BedReading.timestamp <= end)
        .order_by(BedReading.timestamp.asc())
        .all()
    )

    return [
        {
            "timestamp": r.timestamp,
            "average": r.average,
            "valve_state": r.valve_state,
        }
        for r in rows
    ]

# -----------------------------
# 📈 GRAPH DATA (FRONTEND FRIENDLY)
# -----------------------------
@app.get("/api/beds/{bed_id}/graph")
def graph_data(bed_id: str, limit: int = 200, db: Session = Depends(get_db)):

    rows = (
        db.query(BedReading)
        .filter(BedReading.bed_id == bed_id)
        .order_by(BedReading.timestamp.desc())
        .limit(limit)
        .all()
    )

    rows.reverse()

    return {
        "timestamps": [r.timestamp for r in rows],
        "average": [r.average for r in rows],
        "valve": [r.valve_state for r in rows],
    }

# -----------------------------
# 📊 STATS
# -----------------------------
@app.get("/api/beds/{bed_id}/stats")
def stats(bed_id: str, db: Session = Depends(get_db)):

    rows = db.query(BedReading).filter(BedReading.bed_id == bed_id).all()

    if not rows:
        return {"error": "no data"}

    vals = [r.average for r in rows]

    return {
        "count": len(rows),
        "min": min(vals),
        "max": max(vals),
        "avg": sum(vals) / len(vals),
        "last": rows[-1].average
    }

# -----------------------------
# ⚙️ CONFIG GET
# -----------------------------
@app.get("/api/config/{bed_id}")
def get_config(bed_id: str, db: Session = Depends(get_db)):

    config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    if not config:
        config = BedConfigDB(bed_id=bed_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    return {
        "bed_id": bed_id,
        "moisture_threshold": config.moisture_threshold,
        "watering_duration_sec": config.watering_duration_sec,
        "cooldown_sec": config.cooldown_sec,
        "sampling_interval_sec": config.sampling_interval_sec,
    }

# -----------------------------
# ⚙️ CONFIG UPDATE
# -----------------------------
@app.post("/api/config/{bed_id}")
def update_config(bed_id: str, config: BedConfig, db: Session = Depends(get_db)):

    db_config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    if not db_config:
        db_config = BedConfigDB(bed_id=bed_id)
        db.add(db_config)

    for k, v in config.model_dump(exclude_unset=True).items():
        setattr(db_config, k, v)

    db.commit()

    return {"status": "updated"}

# -----------------------------
# 🌧️ SMART WATER DECISION
# -----------------------------
@app.post("/api/should-water")
def should_water(bed_id: str, average_moisture: float, db: Session = Depends(get_db)):

    config = db.query(BedConfigDB).filter(BedConfigDB.bed_id == bed_id).first()

    if not config:
        config = BedConfigDB(bed_id=bed_id)
        db.add(config)
        db.commit()
        db.refresh(config)

    weather = get_weather()

    dry = average_moisture < config.moisture_threshold
    rain = weather["will_rain"]

    return {
        "bed_id": bed_id,
        "water": dry and not rain,
        "soil_dry": dry,
        "rain_expected": rain,
        "weather": weather
    }

# -----------------------------
# 🧪 HEALTH
# -----------------------------
@app.get("/health")
def health():
    return {"status": "alive"}