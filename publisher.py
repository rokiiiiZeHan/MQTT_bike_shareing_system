import json
import random
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from database import fetch_all_bikes

BROKER = "localhost"
PORT = 1883

client = mqtt.Client(client_id="ControlledBikePublisher", protocol=mqtt.MQTTv5)
client.connect(BROKER, PORT)
client.loop_start()

print("Starting controlled bike simulation...")
print("Locked bikes stay still. Unlocked bikes move and consume battery.")
print("=" * 70)


def update_simulated_bike(bike):
    """
    Only unlocked bikes move and consume battery.
    Locked bikes remain stationary and keep the same battery level.
    """
    lat = float(bike["lat"])
    lon = float(bike["lon"])
    battery = int(bike["battery"])
    lock_state = bike["lock"]

    if lock_state == "unlocked":
        lat += random.uniform(-0.0003, 0.0003)
        lon += random.uniform(-0.0003, 0.0003)
        battery = max(0, battery - random.randint(1, 2))

    return {
        "bike_id": bike["bike_id"],
        "lat": lat,
        "lon": lon,
        "battery": battery,
        "lock": lock_state,
    }


try:
    while True:
        bikes = fetch_all_bikes()

        if not bikes:
            print("[INFO] No bikes found in database. Run python database.py first.")

        for bike in bikes:
            updated_bike = update_simulated_bike(bike)

            gps_payload = {
                "bike_id": updated_bike["bike_id"],
                "lat": updated_bike["lat"],
                "lon": updated_bike["lon"],
                "timestamp": datetime.now().isoformat(),
            }

            battery_payload = {
                "bike_id": updated_bike["bike_id"],
                "battery": updated_bike["battery"],
                "timestamp": datetime.now().isoformat(),
            }

            lock_payload = {
                "bike_id": updated_bike["bike_id"],
                "lock_state": updated_bike["lock"],
                "timestamp": datetime.now().isoformat(),
            }

            client.publish(
                f"bike/{updated_bike['bike_id']}/gps",
                json.dumps(gps_payload)
            )
            client.publish(
                f"bike/{updated_bike['bike_id']}/battery",
                json.dumps(battery_payload)
            )
            client.publish(
                f"bike/{updated_bike['bike_id']}/lock",
                json.dumps(lock_payload)
            )

            print(
                f"[PUB] Bike {updated_bike['bike_id']} - "
                f"Pos: ({updated_bike['lat']:.6f}, {updated_bike['lon']:.6f}) - "
                f"Battery: {updated_bike['battery']}% - "
                f"Lock: {updated_bike['lock']}"
            )

        print("-" * 70)
        time.sleep(3)

except KeyboardInterrupt:
    print("\nShutting down publisher...")
    client.loop_stop()
    client.disconnect()