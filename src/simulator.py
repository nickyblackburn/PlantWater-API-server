import requests
import random
import time
import threading
from datetime import datetime, timedelta

SERVER = "http://127.0.0.1:8000"
API_KEY = "your_super_secret_key"
HEADERS = {"x-api-key": API_KEY}

BEDS = [f"bed_{i}" for i in range(1, 5)]

# 🌱 state
soil_state = {bed: random.uniform(300, 800) for bed in BEDS}
watering_state = {bed: None for bed in BEDS}
override_state = {bed: None for bed in BEDS}


# =========================
# 🫀 HEARTBEAT
# =========================
def send_heartbeat(bed_id):
    try:
        requests.post(
            f"{SERVER}/api/node/heartbeat",
            params={"bed_id": bed_id},
            headers=HEADERS
        )
    except Exception as e:
        print("heartbeat failed:", e)


# =========================
# 🌱 SENSOR SIMULATION
# =========================
def simulate_sensor(bed_id):
    base = soil_state[bed_id]

    # natural drying
    base -= random.uniform(0.5, 3)

    now = datetime.utcnow()

    # watering effect
    if watering_state[bed_id] and now < watering_state[bed_id]:
        base += random.uniform(5, 15)

    value = base + random.uniform(-5, 5)
    value = max(200, min(850, value))

    soil_state[bed_id] = value

    sensors = [value + random.uniform(-15, 15) for _ in range(5)]
    avg = sum(sensors) / len(sensors)

    return sensors, avg


# =========================
# 🚰 AUTO WATER CHECK
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
# 📡 SEND SENSOR DATA
# =========================
def send_data(bed_id, sensors, avg, valve_state, override=False):
    payload = {
        "bed_id": bed_id,
        "timestamp": datetime.utcnow().isoformat(),
        "sensors": [float(x) for x in sensors],
        "average": float(avg),
        "valve_state": valve_state,
        "override_active": override,

        # 📡 simulated hardware data
        "rssi": random.randint(-90, -40),
        "battery": round(random.uniform(3.6, 4.2), 2),
    }

    try:
        requests.post(
            f"{SERVER}/api/bed-data",
            json=payload,
            headers=HEADERS
        )
    except Exception as e:
        print("send failed:", e)


# =========================
# 🚰 VALVE LOGIC
# =========================
def apply_watering_effect(bed_id, decision, override=None):
    now = datetime.utcnow()

    # 🟣 OVERRIDE MODE
    if override in ["ON", "OFF"]:

        if override == "ON":
            watering_state[bed_id] = now + timedelta(seconds=999999)
            print(f"🟣 OVERRIDE ON {bed_id}")

        elif override == "OFF":
            watering_state[bed_id] = None
            print(f"🛑 OVERRIDE OFF {bed_id}")

        return

    # 💧 AUTO MODE
    if decision and decision.get("water"):

        duration = 3
        watering_state[bed_id] = now + timedelta(seconds=duration)

        def stop():
            time.sleep(duration)
            print(f"💧 AUTO STOP {bed_id}")

        threading.Thread(target=stop).start()

        print(f"💧 AUTO WATER {bed_id}")



# =========================
# 🔁 MAIN LOOP
# =========================
def run():
    print("🌿 Smart Garden Simulator starting...")

    while True:
        for bed in BEDS:

            # 🫀 heartbeat FIRST (important)
            send_heartbeat(bed)

            # 🌱 sensor logic
            sensors, avg = simulate_sensor(bed)
            decision = check_watering(bed, avg)

            override = override_state.get(bed)

            apply_watering_effect(bed, decision, override)

            now = datetime.utcnow()
            valve = "ON" if watering_state[bed] and now < watering_state[bed] else "OFF"

            send_data(bed, sensors, avg, valve, override is not None)

            print(
                f"{bed} | avg={avg:.1f} | "
                f"valve={valve} | "
                f"heartbeat=✔ | "
                f"rssi simulated"
            )

        time.sleep(2)


if __name__ == "__main__":
    run()