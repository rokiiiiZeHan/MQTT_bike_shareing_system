import asyncio
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import socketio
import threading
import paho.mqtt.client as mqtt

# === Wrapper Socket.IO and FastAPI ===
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)

# === Frontend ===
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === MQTT Config for public test broker ===
BROKER = "localhost"
PORT = 1883
TOPIC_GPS = "bike/{bike_id}/gps"
TOPIC_STATUS = "bike/{bike_id}/status"
TOPIC_BATTERY = "bike/{bike_id}/battery"
TOPIC_LOCK = "bike/{bike_id}/lock"
TOPIC_VIOLATION = "bike/{bike_id}/violation"

LEGAL_ZONES = [#define Legal area
    {
        "zone_id": "zone_1",
        "name": "Main Station",
        "lat": -22.2572774,
        "lon": -45.6963601,
        "radius_km": 0.5
    },
    {
        "zone_id": "zone_2",
        "name": "Shopping Mall",
        "lat": -22.2600000,
        "lon": -45.6980000,
        "radius_km": 0.3
    },
    {
        "zone_id": "zone_3",
        "name": "Park Entrance",
        "lat": -22.2550000,
        "lon": -45.6940000,
        "radius_km": 0.4
    }
]

#Multi-vehicle status management
bikes: Dict[str, dict] = {}##############################

loop = asyncio.get_event_loop()

def haversine(lat1, lon1, lat2, lon2):
    """Calculate the distance between two points"""
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

def check_legal_parking(lat, lon):
    """Check whether the vehicle is in a legal parking area"""
    for zone in LEGAL_ZONES:
        distance = haversine(lat, lon, zone["lat"], zone["lon"])
        if distance <= zone["radius_km"]:
            return True, zone["zone_id"], zone["name"]
    return False, None, None


# === MQTT Callbacks ===
def on_connect(client, userdata, flags, reasonCode, properties):
    print("Connected to MQTT broker")
    # 订阅所有单车的GPS主题（使用通配符）
    client.subscribe("bike/+/gps")
    client.subscribe("bike/+/status")
    client.subscribe("bike/+/battery")
    client.subscribe("bike/+/lock")


def on_message(client, userdata, msg):
    try:
        # Parse topic to get bike_id
        topic_parts = msg.topic.split('/')
        if len(topic_parts) >= 3:
            bike_id = topic_parts[1]
            message_type = topic_parts[2]
        else:
            return

        payload = json.loads(msg.payload.decode())
        if bike_id not in bikes:
            bikes[bike_id] = {
                "bike_id": bike_id,
                "lat": -22.2572774,
                "lon": -45.6963601,
                "status": "safe",
                "battery": 100,
                "lock": "locked",
                "last_update": datetime.now().isoformat(),
                "violation_start_time": None,
                "violation_warning_sent": False,
                "severe_violation_recorded": False,
                "current_zone": None
            }

        if message_type == "gps":
            bikes[bike_id]["lat"] = payload.get("lat", bikes[bike_id]["lat"])
            bikes[bike_id]["lon"] = payload.get("lon", bikes[bike_id]["lon"])

            is_legal, zone_id, zone_name = check_legal_parking(
                bikes[bike_id]["lat"],
                bikes[bike_id]["lon"]
            )
            old_status = bikes[bike_id]["status"]
            bikes[bike_id]["status"] = "safe" if is_legal else "out_of_zone"
            bikes[bike_id]["current_zone"] = zone_name

            print(f"[MQTT] Bike {bike_id} - Location: ({bikes[bike_id]['lat']:.6f}, {bikes[bike_id]['lon']:.6f}) - Status: {bikes[bike_id]['status']} - Zone: {zone_name}")
        elif message_type == "battery":
            bikes[bike_id]["battery"] = payload.get("battery", bikes[bike_id]["battery"])
            print(f"[MQTT] Bike {bike_id} - Battery: {bikes[bike_id]['battery']}%")
        elif message_type == "lock":
            bikes[bike_id]["lock"] = payload.get("lock_state", bikes[bike_id]["lock"])
            print(f"[MQTT] Bike {bike_id} - Lock: {bikes[bike_id]['lock']}")

        bikes[bike_id]["last_update"] = datetime.now().isoformat()#################

        asyncio.run_coroutine_threadsafe(
            sio.emit("location_update", {
                "bike_id": bike_id,
                "lat": bikes[bike_id]["lat"],
                "lon": bikes[bike_id]["lon"],
                "status": bikes[bike_id]["status"],
                "battery": bikes[bike_id]["battery"],
                "lock": bikes[bike_id]["lock"],
                "zone": bikes[bike_id]["current_zone"],
                "last_update": bikes[bike_id]["last_update"]
            }),
            loop
        )

    except Exception as e:
        print(f"Error processing MQTT message: {e}")


def mqtt_thread():
    client = mqtt.Client(client_id="BikeSubscriber", protocol=mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER, PORT, 60)
    client.loop_forever()


# === FastAPI Routes ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
@app.get("/api/bikes")
async def get_all_bikes():
    """Get all bicycle statuses"""
    return {"bikes": list(bikes.values())}

# === Start MQTT Thread ===
threading.Thread(target=mqtt_thread, daemon=True).start()
