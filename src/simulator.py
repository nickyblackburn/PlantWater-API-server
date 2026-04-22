import requests
import random
import time
import threading
from datetime import datetime, timedelta

SERVER = "http://127.0.0.1:8000"
API_KEY = "your_super_secret_key"
HEADERS = {"x-api-key": API_KEY}

BEDS = [f"bed_{i}" for i in range(1, 5)]

soil_state = {bed: random.uniform(300, 800) for bed in BEDS}
watering_state = {bed: None for bed in BEDS}

# 🌿 NEW: external override system
override_state = {bed: None for bed in BEDS}


# =========================
# SENSOR SIMULATION
# =========================
def simulate_sensor(bed_id):
    base = soil_state[bed_id]

    # natural drying
    base -= random.uniform(0.5, 3)

    now = datetime.utcnow()

    # 💧 normal watering effect
    if watering_state[bed_id] and now < watering_state[bed_id]:
        base += random.uniform(5, 15)

    value = base + random.uniform(-5, 5)
    value = max(200, min(850, value))

    soil_state[bed_id] = value

    sensors = [value + random.uniform(-15, 15) for _ in range(5)]
    avg = sum(sensors) / len(sensors)

    return sensors, avg


# =========================
# SEND DATA
# =========================
def send_data(bed_id, sensors, avg, valve_state, override=False):
    payload = {
        "bed_id": bed_id,
        "timestamp": datetime.utcnow().isoformat(),
        "sensors": [float(x) for x in sensors],
        "average": float(avg),
        "valve_state": valve_state,
        "override_active": override,
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
        print("server down:", e)
        return None


# =========================
# CHECK AUTO WATER
# =========================
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


# =========================
# REPORT VALVE CHANGE
# =========================
def report_valve_change(bed_id, valve_state):
    try:
        requests.post(
            f"{SERVER}/api/beds/{bed_id}/water-cycle",
            params={"valve_state": valve_state},
            headers=HEADERS
        )
    except:
        pass


# =========================
# APPLY WATERING LOGIC
# =========================
def apply_watering_effect(bed_id, decision, override=None):
    now = datetime.utcnow()

    # 🌿 OVERRIDE SYSTEM (NEW PRIORITY)
    if override in ["ON", "OFF"]:

        if override == "ON":
            watering_state[bed_id] = now + timedelta(seconds=999999)

            report_valve_change(bed_id, "ON")

            print(f"🟣 OVERRIDE ON → {bed_id} forced watering")

        elif override == "OFF":
            watering_state[bed_id] = None

            report_valve_change(bed_id, "OFF")

            print(f"🛑 OVERRIDE OFF → {bed_id} forced stop")

        return True

    # 💧 NORMAL AUTO MODE
    if decision and decision.get("water"):

        duration = 3
        watering_state[bed_id] = now + timedelta(seconds=duration)

        report_valve_change(bed_id, "ON")

        def stop_later():
            time.sleep(duration)
            report_valve_change(bed_id, "OFF")

        threading.Thread(target=stop_later).start()

        print(f"💧 AUTO water {bed_id} for {duration}s")

        return True

    return False


# =========================
# MAIN LOOP
# =========================
def run():
    print("🌿 irrigation simulator starting...")

    while True:
        for bed in BEDS:

            sensors, avg = simulate_sensor(bed)
            decision = check_watering(bed, avg)

            override = override_state.get(bed)

            applied_override = apply_watering_effect(
                bed,
                decision,
                override
            )

            now = datetime.utcnow()

            valve = "ON" if watering_state[bed] and now < watering_state[bed] else "OFF"

            send_data(
                bed,
                sensors,
                avg,
                valve,
                override=override is not None
            )

            print(
                f"{bed} | avg={avg:.1f} | "
                f"valve={valve} | "
                f"override={override} | "
                f"auto={decision.get('water') if decision else None}"
            )

        time.sleep(2)


if __name__ == "__main__":
    run()