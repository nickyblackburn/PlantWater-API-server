import requests
import random
import time
from datetime import datetime, timedelta

SERVER = "http://127.0.0.1:8000"
BEDS = [
    "bed_1", "bed_2", "bed_3", "bed_4", "bed_5",
    "bed_6", "bed_7", "bed_8", "bed_9", "bed_10",
    "bed_11", "bed_12", "bed_13", "bed_14", "bed_15",
    "bed_16", "bed_17", "bed_18", "bed_19", "bed_20"
]

soil_state = {
    bed: random.uniform(300, 800)
    for bed in BEDS
}

# 💧 track irrigation "active watering"
watering_state = {
    bed: None  # stores end time if watering
    for bed in BEDS
}


def simulate_sensor(bed_id):
    """
    soil slowly dries + irrigation slowly raises it
    """

    base = soil_state[bed_id]

    # 🌵 drying effect
    base -= random.uniform(0.5, 3)

    # 💧 if watering is active, gently increase moisture
    now = datetime.utcnow()
    if watering_state[bed_id] and now < watering_state[bed_id]:
        base += random.uniform(5, 15)

    # noise
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
        r = requests.post(f"{SERVER}/api/bed-data", json=payload)
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
            }
        )
        return r.json()
    except:
        return None


def apply_watering_effect(bed_id, decision):
    """
    when watering happens, simulate duration
    """

    if decision and decision.get("water"):

        duration = 3  # match your backend default

        watering_state[bed_id] = datetime.utcnow() + timedelta(seconds=duration)
        # when watering starts
        report_valve_change(bed_id, "ON")

        # when watering ends
        report_valve_change(bed_id, "OFF")

        print(f"💧 {bed_id} watering for {duration}s")


def report_valve_change(bed_id, valve_state):
    requests.post(
        f"{SERVER}/api/beds/{bed_id}/water-cycle",
        params={"valve_state": valve_state}
    )


def run():
    print("🌿 irrigation simulator starting...")

    while True:

        for bed in BEDS:

            sensors, avg = simulate_sensor(bed)

            decision = check_watering(bed, avg)

            if decision:
                apply_watering_effect(bed, decision)

            # 🚰 correct valve state reporting
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