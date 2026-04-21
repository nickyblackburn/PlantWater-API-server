import requests
import random
import time
from datetime import datetime, timedelta

SERVER = "http://127.0.0.1:8000"

API_KEY = "your_super_secret_key"
HEADERS = {"x-api-key": API_KEY}

BEDS = [f"bed_{i}" for i in range(1, 5)]

soil_state = {
    bed: random.uniform(300, 800)
    for bed in BEDS
}

watering_state = {
    bed: None
    for bed in BEDS
}


def simulate_sensor(bed_id):
    base = soil_state[bed_id]

    # 🌵 drying
    base -= random.uniform(0.5, 3)

    # 💧 watering effect
    now = datetime.utcnow()
    if watering_state[bed_id] and now < watering_state[bed_id]:
        base += random.uniform(5, 15)

    value = base + random.uniform(-5, 5)
    value = max(200, min(850, value))

    soil_state[bed_id] = value

    sensors = [value + random.uniform(-15, 15) for _ in range(5)]
    avg = sum(sensors) / len(sensors)

    return sensors, avg


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
            headers=HEADERS   # 🔐 PROTECTED
        )
        return r.json()
    except Exception as e:
        print("server down:", e)
        return None


def check_watering(bed_id, avg):
    try:
        r = requests.post(
            f"{SERVER}/api/should-water",
            params={
                "bed_id": bed_id,
                "average_moisture": avg
            },
            headers=HEADERS   # 🔐 PROTECTED
        )
        return r.json()
    except:
        return None


def report_valve_change(bed_id, valve_state):
    try:
        requests.post(
            f"{SERVER}/api/beds/{bed_id}/water-cycle",
            params={"valve_state": valve_state},
            headers=HEADERS   # 🔐 PROTECTED
        )
    except:
        pass


def apply_watering_effect(bed_id, decision):
    if decision and decision.get("water"):

        duration = 3
        watering_state[bed_id] = datetime.utcnow() + timedelta(seconds=duration)

        # 🟢 start
        report_valve_change(bed_id, "ON")

        # 🔴 stop AFTER duration (this was missing realism 👀)
        def stop_later():
            time.sleep(duration)
            report_valve_change(bed_id, "OFF")

        import threading
        threading.Thread(target=stop_later).start()

        print(f"💧 {bed_id} watering for {duration}s")


def run():
    print("🌿 irrigation simulator starting...")

    while True:
        for bed in BEDS:

            sensors, avg = simulate_sensor(bed)
            decision = check_watering(bed, avg)

            if decision:
                apply_watering_effect(bed, decision)

            now = datetime.utcnow()
            valve = "ON" if watering_state[bed] and now < watering_state[bed] else "OFF"

            send_data(bed, sensors, avg, valve)

            print(
                f"{bed} | avg={avg:.1f} | "
                f"valve={valve} | "
                f"water={decision.get('water') if decision else None}"
            )

        time.sleep(2)


if __name__ == "__main__":
    run()