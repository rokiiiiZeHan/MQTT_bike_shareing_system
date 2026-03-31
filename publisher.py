import paho.mqtt.client as mqtt 
import random
import math
import time
import json

BROKER = "test.mosquitto.org"
PORT = 1883
TOPIC_UPDATE = "bike/gps/update/cuh405_demo_2026"

lat_ref = 18.4022396560773
lon_ref = 110.01475702107639
safe_radius_km = 0.5

lat = lat_ref
lon = lon_ref

client = mqtt.Client(client_id="BikePublisher", protocol=mqtt.MQTTv5)
client.connect(BROKER, PORT)
client.loop_start()


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return R * c


try:
    while True:
        lat += random.uniform(-0.0005, 0.0005)
        lon += random.uniform(-0.0005, 0.0005)

        distance = haversine(lat, lon, lat_ref, lon_ref)
        status = "safe" if distance <= safe_radius_km else "out_of_zone"

        payload = json.dumps({"lat": lat, "lon": lon, "status": status})

        client.publish(TOPIC_UPDATE, payload)

        print(f"Publishing -> {payload}")

        time.sleep(3)

except KeyboardInterrupt:
    print("Shutting down publisher...")
    client.loop_stop()
    client.disconnect()
