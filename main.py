import asyncio
import json
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
BROKER = "broker.emqx.io"
PORT = 1883
TOPIC_UPDATE = "bike/gps/update/cuh405_demo_2026"

state = {"lat": -22.2572774, "lon": -45.6963601, "status": "safe"}

loop = asyncio.get_event_loop()


# === MQTT Callbacks ===
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT")
        client.subscribe(TOPIC_UPDATE, qos=1)
    else:
        print(f"Failed to connect to MQTT, return code: {rc}")

def on_disconnect(client, userdata, rc):
    print(f"MQTT disconnected, return code: {rc}")

def on_subscribe(client, userdata, mid, granted_qos):
    print(f"Subscribed to topic with mid: {mid}, granted QoS: {granted_qos}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        state["lat"] = payload.get("lat", state["lat"])
        state["lon"] = payload.get("lon", state["lon"])
        state["status"] = payload.get("status", state["status"])

        print(f"[MQTT] Received: {payload}")

        asyncio.run_coroutine_threadsafe(
            sio.emit("location_update", state),
            loop,
        )

    except Exception as e:
        print(f"Error processing MQTT message: {e}")


def mqtt_thread():
    try:
        print("Starting MQTT thread")
        client = mqtt.Client(protocol=mqtt.MQTTv311)
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect
        client.on_subscribe = on_subscribe

        print("Attempting to connect to MQTT broker")
        client.connect(BROKER, PORT, 60)
        print("MQTT client loop starting")
        client.loop_forever()
    except Exception as e:
        print(f"MQTT thread error: {e}")


# === Start MQTT Thread ===
threading.Thread(target=mqtt_thread, daemon=True).start()


# === FastAPI Routes ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
