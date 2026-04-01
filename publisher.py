import paho.mqtt.client as mqtt
import random
import math
import time
import json
from datetime import datetime

BROKER = "localhost"
PORT = 1883

BIKES = [
    {
        "bike_id": "B001",
        "lat": -22.2572774,
        "lon": -45.6963601,
        "battery": 100,  # ← 初始电量
        "lock": "locked"  # ← 也可以加锁状态
    },
    {
        "bike_id": "B002",
        "lat": -22.2600000,
        "lon": -45.6980000,
        "battery": 95,
        "lock": "locked"
    },
    {
        "bike_id": "B003",
        "lat": -22.2550000,
        "lon": -45.6940000,
        "battery": 88,
        "lock": "unlocked"
    },
]#######################待会改成数据库


client = mqtt.Client(client_id="MultiBikePublisher", protocol=mqtt.MQTTv5)
client.connect(BROKER, PORT)
client.loop_start()

print("Starting multi-bike simulation...")
print("="*60)

try:
    while True:
        for bike in BIKES:
            # Move a short distance randomly
            if bike["lock"] == "unlocked":
                bike["lat"] += random.uniform(-0.0003, 0.0003)
                bike["lon"] += random.uniform(-0.0003, 0.0003)
                #Simulated Battery Consumption
                bike["battery"] = max(0, bike["battery"] - random.uniform(0.1, 0.5))
            else:
                bike["battery"] = max(0, bike["battery"] - random.uniform(0.01, 0.05))
            battery = int(bike["battery"])
            lock_state = bike["lock"]

            # Simulate car lock status
            if random.random() < 0.05:
                if bike["lock"] == "locked":
                    bike["lock"] = "unlocked"
                    print(f"[Bike {bike['bike_id']}] Unlocked, ready to move")
                else:
                    bike["lock"] = "locked"
                    print(f"[Bike {bike['bike_id']}] Locked")

            gps_payload = {
                "bike_id": bike["bike_id"],
                "lat": bike["lat"],
                "lon": bike["lon"],
                "timestamp": datetime.now().isoformat()
            }

            battery_payload = {
                "bike_id": bike["bike_id"],
                "battery": battery,
                "timestamp": datetime.now().isoformat()
            }

            lock_payload = {
                "bike_id": bike["bike_id"],
                "lock_state": lock_state,
                "timestamp": datetime.now().isoformat()
            }

            client.publish(f"bike/{bike['bike_id']}/gps", json.dumps(gps_payload))
            client.publish(f"bike/{bike['bike_id']}/battery", json.dumps(battery_payload))
            client.publish(f"bike/{bike['bike_id']}/lock", json.dumps(lock_payload))

            print(f"[PUB] Bike {bike['bike_id']} - "
                  f"Pos: ({bike['lat']:.6f}, {bike['lon']:.6f}) - "
                  f"Battery: {battery}% - Lock: {lock_state}")

        print("-" * 60)
        time.sleep(3)

except KeyboardInterrupt:
    print("\nShutting down publisher...")
    client.loop_stop()
    client.disconnect()
