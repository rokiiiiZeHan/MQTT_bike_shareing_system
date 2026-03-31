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
BROKER = "test.mosquitto.org"
PORT = 1883
TOPIC_UPDATE = "bike/gps/update/cuh405_demo_2026"

state = {"lat": 18.4022396560773, "lon": 110.01475702107639, "status": "safe"}

loop = asyncio.get_event_loop()


# === MQTT Callbacks ===
def on_connect(client, userdata, flags, reasonCode, properties):
    print("Connected to MQTT")
    client.subscribe(TOPIC_UPDATE)


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
    client = mqtt.Client(client_id="BikeSubscriber", protocol=mqtt.MQTTv5)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(BROKER, PORT, 60)
    client.loop_forever()


# === Start MQTT Thread ===
threading.Thread(target=mqtt_thread, daemon=True).start()


# === FastAPI Routes ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
