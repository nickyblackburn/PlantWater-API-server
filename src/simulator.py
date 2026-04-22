import requests
import random
import time
import threading
from datetime import datetime, timedelta

# ============================================================
# 🌍 CONFIG
# ============================================================
SERVER = "http://127.0.0.1:8000"
API_KEY = "your_super_secret_key"
HEADERS = {"x-api-key": API_KEY}

BEDS = [f"bed_{i}" for i in range(1, 5)]

# ============================================================
# 🌱 STATE
# ============================================================
soil_state = {
    bed: random.uniform(300, 800)
    for bed in BEDS
}

watering_state = {
    bed: None  # timestamp until watering ends
    for bed in BEDS
}

last_override_state = {
    bed: None
    for bed in BEDS
}

# ============================================================
# 🌡 SENSOR SIMULATION
# ============================================================
def simulate_sensor(bed_id):
    base = soil_state[bed_id]

    # slow drying
    base -= random.uniform(0.5, 3)

    # watering boost
    now = datetime.utcnow()
    if watering_state[bed_id] and now < watering_state[bed_id]:
        base += random.uniform(5, 15)

    # clamp realistic bounds
    value = max(200, min(850, base + random.uniform(-5, 5)))
    soil_state[bed_id] = value

    sensors = [value + random.uniform(-15, 15) for _ in range(5)]
    avg = sum(sensors) / len(sensors)

    return sensors, avg


# ============================================================
# 📡 SEND SENSOR DATA
# ============================================================
def send_data(bed_id, sensors, avg, valve_state):
    payload = {
        "bed_id": bed_id,
        "timestamp": datetime.utcnow().isoformat(),
        "sensors": [float(x) for x in sensors],
        "average": float(avg),
        "valve_state": valve_state,
        "rssi": -50
    }

    try:
        r = requests.post(
            f"{SERVER}/api/bed-data",
            json=payload,
            headers=HEADERS
        )
        return r.json()
    except Exception as e:
        print("❌ server down:", e)
        return None


# ============================================================
# 🧠 AI WATER DECISION
# ============================================================
def check_watering(bed_id, avg):
    try:
        r = requests.post(
            f"{SERVER}/api/should-water",
            params={
                "bed_id": bed_id,
                "average_moisture": avg
            },
            headers=HEADERS
        )
        return r.json()
    except:
        return None


# ============================================================
# 🚰 VALVE OVERRIDE CHECK
# ============================================================
def check_override(bed_id):
    try:
        r = requests.get(f"{SERVER}/api/valve/{bed_id}")
        return r.json().get("valve_state")
    except:
        return None


# ============================================================
# 📣 REPORT VALVE CHANGE (FOR LIFECYCLE TRACKING)
# ============================================================
def report_valve_change(bed_id, state):
    try:
        requests.post(
            f"{SERVER}/api/beds/{bed_id}/water-cycle",
            params={"valve_state": state},
            headers=HEADERS
        )
    except:
        pass


# ============================================================
# 💧 WATERING EFFECT SIMULATION
# ============================================================
def apply_watering_effect(bed_id, decision):
    if not decision or not decision.get("water"):
        return

    duration = 3
    now = datetime.utcnow()

    watering_state[bed_id] = now + timedelta(seconds=duration)

    report_valve_change(bed_id, "ON")

    def stop():
        time.sleep(duration)
        report_valve_change(bed_id, "OFF")

    threading.Thread(target=stop, daemon=True).start()

    print(f"💧 AUTO WATER: {bed_id} for {duration}s")


# ============================================================
# 🚨 OVERRIDE LOGGER
# ============================================================
def detect_override(bed_id, valve_state):
    last = last_override_state[bed_id]

    if valve_state != last:

        if valve_state == "ON":
            print(f"🚨 OVERRIDE: {bed_id} forced ON")

        elif valve_state == "OFF":
            print(f"🛑 OVERRIDE: {bed_id} forced OFF")

        else:
            print(f"⚙️ OVERRIDE: {bed_id} -> {valve_state}")

        last_override_state[bed_id] = valve_state


# ============================================================
# 🔁 MAIN LOOP
# ============================================================
def run():
    print("🌿 Smart Irrigation Simulator starting...")

    while True:
        for bed in BEDS:

            # -------------------------
            # SENSOR SIMULATION
            # -------------------------
            sensors, avg = simulate_sensor(bed)

            # -------------------------
            # AI DECISION
            # -------------------------
            decision = check_watering(bed, avg)

            if decision:
                apply_watering_effect(bed, decision)

            # -------------------------
            # OVERRIDE STATE
            # -------------------------
            override = check_override(bed)

            if override:
                detect_override(bed, override)

            # -------------------------
            # FINAL VALVE STATE
            # -------------------------
            now = datetime.utcnow()

            auto_valve = "ON" if watering_state[bed] and now < watering_state[bed] else "OFF"

            final_valve = override if override else auto_valve

            # -------------------------
            # SEND DATA TO SERVER
            # -------------------------
            send_data(bed, sensors, avg, final_valve)

            # -------------------------
            # LOG OUTPUT
            # -------------------------
            print(
                f"{bed} | "
                f"avg={avg:.1f} | "
                f"valve={final_valve} | "
                f"ai={decision.get('water') if decision else None}"
            )

        time.sleep(2)


# ============================================================
# 🚀 START
# ============================================================
if __name__ == "__main__":
    run()