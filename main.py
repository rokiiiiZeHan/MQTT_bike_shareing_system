import asyncio
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import socketio
import threading
import paho.mqtt.client as mqtt
from database import (
    fetch_all_bikes,
    fetch_admin_by_username,
    fetch_recent_violations,
    init_db,
    insert_violation,
    upsert_bike,
)

# === Wrapper Socket.IO and FastAPI ===
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
app = FastAPI()
# Production should use a strong secret key.
app.add_middleware(SessionMiddleware, secret_key="admin-demo-secret-key")
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

LEGAL_ZONES = [
    {
        "zone_id": "zone_1",
        "name": "Lian Port",
        "lat": 18.402239,  # 陵水黎安港纬度
        "lon": 110.014757,  # 陵水黎安港经度
        "radius_km": 0.05
    },
    {
        "zone_id": "zone_2",
        "name": "Li'an Town Government",
        "lat": 18.405000,
        "lon": 110.016000,
        "radius_km": 0.04
    },
    {
        "zone_id": "zone_3",
        "name": "Li'an Central Primary School",
        "lat": 18.400000,
        "lon": 110.012000,
        "radius_km": 0.04
    },
]

#Multi-vehicle status management
bikes: Dict[str, dict] = {}##############################

# Violation record
violations: List[dict] = []


loop = asyncio.get_event_loop()


def verify_admin_credentials(username, password):
    """Validate admin credentials using a plain-text password check for now."""
    admin = fetch_admin_by_username(username)
    if not admin:
        return False

    # Prototype only: production should use hashed passwords.
    return password == admin["password_hash"]


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


def check_violation(bike_id):
    """检查违规并记录"""
    bike = bikes.get(bike_id)
    if not bike:
        return

    now = datetime.now()
    is_legal = bike["status"] == "safe"
    is_locked = bike["lock"] == "locked"

    # 如果车辆在非法区域，开始计时
    if is_locked and not is_legal:
        if bike["violation_start_time"] is None:
            bike["violation_start_time"] = now
            bike["violation_warning_sent"] = False
        else:
            # 计算违规时长
            violation_duration = (now - bike["violation_start_time"]).seconds / 60  # 分钟

            # 5分钟后发送警告
            if violation_duration >= 5 and not bike["violation_warning_sent"]:
                violation_record = {
                    "bike_id": bike_id,
                    "type": "warning",
                    "message": f"Bike {bike_id} has been parked illegally for {violation_duration:.0f} minutes",
                    "timestamp": now.isoformat(),
                    "location": {"lat": bike["lat"], "lon": bike["lon"]}
                }
                violations.append(violation_record)
                insert_violation(violation_record)
                bike["violation_warning_sent"] = True

                # 发送违规通知到MQTT
                asyncio.run_coroutine_threadsafe(
                    publish_violation(bike_id, violation_record),
                    loop
                )

            # 10分钟后记录严重违规
            if violation_duration >= 10:
                violation_record = {
                    "bike_id": bike_id,
                    "type": "severe",
                    "message": f"BIKE {bike_id} SEVERE VIOLATION: Illegal parking for {violation_duration:.0f} minutes",
                    "timestamp": now.isoformat(),
                    "location": {"lat": bike["lat"], "lon": bike["lon"]}
                }
                violations.append(violation_record)
                insert_violation(violation_record)

                # 只记录一次严重违规
                if not bike.get("severe_violation_recorded"):
                    bike["severe_violation_recorded"] = True
                    asyncio.run_coroutine_threadsafe(
                        publish_violation(bike_id, violation_record),
                        loop
                    )
    else:
        # 车辆回到安全区域，重置违规计时
        if bike["violation_start_time"] is not None:
            violation_duration = (now - bike["violation_start_time"]).seconds / 60
            if violation_duration > 0:
                print(f"[INFO] Bike {bike_id} returned to legal zone after {violation_duration:.0f} minutes")
            bike["violation_start_time"] = None
            bike["violation_warning_sent"] = False
            bike["severe_violation_recorded"] = False


async def publish_violation(bike_id, violation):
    """Publish illegal information to MQTT"""
    client = mqtt.Client(client_id="ViolationPublisher", protocol=mqtt.MQTTv5)
    try:
        client.connect(BROKER, PORT, 60)
        topic = TOPIC_VIOLATION.format(bike_id=bike_id)
        client.publish(topic, json.dumps(violation))
        client.disconnect()
    except Exception as e:
        print(f"Error publishing violation: {e}")


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
                "lat": 18.402239,
                "lon": 110.014757,
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

            # Check for violations
            check_violation(bike_id)

            print(f"[MQTT] Bike {bike_id} - Location: ({bikes[bike_id]['lat']:.6f}, {bikes[bike_id]['lon']:.6f}) - Status: {bikes[bike_id]['status']} - Zone: {zone_name}")

        elif message_type == "battery":
            bikes[bike_id]["battery"] = payload.get("battery", bikes[bike_id]["battery"])
            print(f"[MQTT] Bike {bike_id} - Battery: {bikes[bike_id]['battery']}%")
            if bikes[bike_id]["battery"] < 20:
                print(f"[WARNING] Bike {bike_id} battery low: {bikes[bike_id]['battery']}%")

        elif message_type == "lock":
            bikes[bike_id]["lock"] = payload.get("lock_state", bikes[bike_id]["lock"])
            print(f"[MQTT] Bike {bike_id} - Lock: {bikes[bike_id]['lock']}")

        bikes[bike_id]["last_update"] = datetime.now().isoformat()#################

        upsert_bike(
            {
                "bike_id": bikes[bike_id]["bike_id"],
                "lat": bikes[bike_id]["lat"],
                "lon": bikes[bike_id]["lon"],
                "status": bikes[bike_id]["status"],
                "battery": bikes[bike_id]["battery"],
                "lock": bikes[bike_id]["lock"],
                "current_zone": bikes[bike_id]["current_zone"],
                "last_update": bikes[bike_id]["last_update"],
            }
        )

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

        if bikes[bike_id].get("violation_start_time") and bikes[bike_id]["status"] == "out_of_zone":
            violation_duration = (datetime.now() - datetime.fromisoformat(
                bikes[bike_id]["violation_start_time"])).seconds / 60
            if violation_duration >= 5:
                asyncio.run_coroutine_threadsafe(
                    sio.emit("violation_alert", {
                        "bike_id": bike_id,
                        "duration_minutes": int(violation_duration),
                        "location": {"lat": bikes[bike_id]["lat"], "lon": bikes[bike_id]["lon"]}
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


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": None},
    )


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not verify_admin_credentials(username, password):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "Invalid username or password"},
        )

    request.session["admin_user"] = username
    # Next step: add a proper logout flow and tighten session handling.
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if "admin_user" not in request.session:
        return RedirectResponse(url="/admin/login", status_code=303)

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "bikes": fetch_all_bikes(),
            "violations": fetch_recent_violations(50),
        },
    )


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_user", None)
    return RedirectResponse(url="/admin/login", status_code=303)


@app.get("/api/bikes")
async def get_all_bikes():
    """Get all bicycle statuses"""
    return {"bikes": fetch_all_bikes()}
@app.get("/api/violations")
async def get_violations():
    """Get violation records"""
    return {"violations": fetch_recent_violations(50)}
@app.get("/api/zones")
async def get_zones():
    """Obtain legal parking areas"""
    return {"zones": LEGAL_ZONES}

# === Initialize Database ===
init_db()

# === Start MQTT Thread ===
threading.Thread(target=mqtt_thread, daemon=True).start()
