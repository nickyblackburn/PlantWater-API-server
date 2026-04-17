import requests
import random
import time
from datetime import datetime

SERVER = "http://127.0.0.1:8000"

BEDS = ["bed_1", "bed_2", "bed_3", "bed_4"]

# 🌱 simulate soil state per bed
soil_state = {
    "bed_1": 600,
    "bed_2": 500,
    "bed_3": 450,
    "bed_4": 550,
}


def simulate_sensor(bed_id):
    """
    fake soil behavior:
    - slowly dries out
    - watering increases moisture
    """

    base = soil_state[bed_id]

    # 🌿 natural drying over time
    base -= random.uniform(1, 5)

    # 💧 noise
    noise = random.uniform(-10, 10)

    value = base + noise

    # clamp
    value = max(200, min(800, value))

    soil_state[bed_id] = value

    # simulate 5 sensors per bed
    sensors = [
        value + random.uniform(-20, 20)
        for _ in range(5)
    ]

    avg = sum(sensors) / len(sensors)

    return sensors, avg


def send_data(bed_id, sensors, avg):
    payload = {
    "bed_id": bed_id,
    "timestamp": datetime.utcnow().isoformat(),
    "sensors": [float(x) for x in sensors],
    "average": float(avg),
    "valve_state": "OFF",
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
    turns valve ON/OFF based on server decision
    """
    valve_state = {bed: "OFF" for bed in BEDS}
    watering_timer = {bed: 0 for bed in BEDS}
    
    if decision and decision.get("water"):

        # turn valve ON
        valve_state[bed_id] = "ON"

        # simulate watering effect
        soil_state[bed_id] += random.uniform(40, 100)

        # set watering duration (cycles)
        watering_timer[bed_id] = 3

    else:
        # countdown watering timer
        if watering_timer[bed_id] > 0:
            watering_timer[bed_id] -= 1
        else:
            valve_state[bed_id] = "OFF"


def run():
    print("🌿 irrigation simulator starting...")

    while True:
        for bed in BEDS:

            sensors, avg = simulate_sensor(bed)

            send_data(bed, sensors, avg)

            decision = check_watering(bed, avg)

            if decision:
                apply_watering_effect(bed, decision)

                print(
                    f"{bed} | avg={avg:.1f} | "
                    f"water={decision['water']} | "
                    f"rain={decision['rain_expected']}"
                )

        time.sleep(2)


if __name__ == "__main__":
    run()