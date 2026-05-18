import asyncio
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List
from fastapi import FastAPI, Form, Request, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import socketio
import threading
import paho.mqtt.client as mqtt
from database import (
    create_or_get_user,
    create_ride,
    fetch_active_ride,
    fetch_all_bikes,
    fetch_admin_by_username,
    fetch_bike_by_id,
    fetch_recent_violations,
    fetch_rides_by_user,
    fetch_user_by_id,
    finish_ride,
    init_db,
    insert_violation,
    update_bike_lock,
    update_user_balance,
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
        "name": "Teaching Building Parking Zone",
        "lat": 18.4235,
        "lon": 110.0395,
        "radius_km": 0.06
    },
    {
        "zone_id": "zone_2",
        "name": "Dormitory Parking Zone",
        "lat": 18.4248,
        "lon": 110.0399,
        "radius_km": 0.05
    },
    {
        "zone_id": "zone_3",
        "name": "Canteen Parking Zone",
        "lat": 18.4228,
        "lon": 110.0405,
        "radius_km": 0.05
    },
]

#Multi-vehicle status management
bikes: Dict[str, dict] = {}##############################

# Violation record
violations: List[dict] = []


loop = asyncio.get_event_loop()

class UserLoginRequest(BaseModel):
    username: str
    password: str

class StartRideRequest(BaseModel):
    user_id: int
    bike_id: str = "B001"
    pricing_mode: str = "pay_as_you_go"

class EndRideRequest(BaseModel):
    user_id: int


def calculate_pay_as_you_go_cost(duration_min: int) -> float:
    """
    Pay-as-you-go pricing:
    First 10 minutes: $1.00
    After 10 minutes: $0.20 per extra minute.
    """
    base_price = 1.00
    included_minutes = 10
    extra_rate_per_minute = 0.20

    extra_minutes = max(0, duration_min - included_minutes)
    return round(base_price + extra_minutes * extra_rate_per_minute, 2)


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
            violation_duration = (
                datetime.now() - bikes[bike_id]["violation_start_time"]
            ).seconds / 60
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
# 1. 用户登录相关
@app.post("/api/user/login")
async def user_login(data: UserLoginRequest):
    """
    User login/register API.
    If the username does not exist, create a new account with $50 balance.
    If the username exists, validate the password.
    """
    username = data.username.strip()
    password = data.password.strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    user = create_or_get_user(username, password)

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid password")

    return {"user": user}


@app.get("/api/user/{user_id}")
async def get_user(user_id: int):
    """Get user profile and balance."""
    user = fetch_user_by_id(user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {"user": user}


# 2. 骑行和计费相关
@app.post("/api/ride/start")
async def start_ride(data: StartRideRequest):
    """
    Start a ride for a user.
    Version 2 uses pay-as-you-go pricing only.
    """
    user = fetch_user_by_id(data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    active_ride = fetch_active_ride(data.user_id)
    if active_ride:
        raise HTTPException(status_code=400, detail="You already have an active ride")

    bike = fetch_bike_by_id(data.bike_id)

    # For prototype stability:
    # If MQTT publisher has not created B001 yet, create a default demo bike.
    if not bike:
        upsert_bike(
            {
                "bike_id": data.bike_id,
                "lat": 18.4235,
                "lon": 110.0395,
                "status": "safe",
                "battery": 100,
                "lock": "locked",
                "current_zone": "Lingshui Campus",
                "last_update": datetime.utcnow().isoformat(),
            }
        )
        bike = fetch_bike_by_id(data.bike_id)

    if bike["lock"] == "unlocked":
        raise HTTPException(status_code=400, detail="Bike is already unlocked")

    if bike["battery"] <= 10:
        raise HTTPException(status_code=400, detail="Bike battery is too low")

    if bike["status"] != "safe":
        raise HTTPException(status_code=400, detail="Bike is not in a legal parking zone")

    ride = create_ride(
        user_id=data.user_id,
        bike_id=data.bike_id,
        pricing_mode=data.pricing_mode,
    )

    update_bike_lock(data.bike_id, "unlocked")

    return {
        "message": "Ride started",
        "ride": ride,
        "bike": fetch_bike_by_id(data.bike_id),
    }


@app.post("/api/ride/end")
async def end_ride(data: EndRideRequest):
    """
    End the active ride, calculate final cost,
    deduct user balance, and store order record.
    """
    user = fetch_user_by_id(data.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    active_ride = fetch_active_ride(data.user_id)
    if not active_ride:
        raise HTTPException(status_code=404, detail="No active ride found")

    start_time = datetime.fromisoformat(active_ride["start_time"])
    end_time = datetime.utcnow()

    duration_min = max(1, int((end_time - start_time).total_seconds() // 60))

    ride_cost = calculate_pay_as_you_go_cost(duration_min)

    bike = fetch_bike_by_id(active_ride["bike_id"])
    relocation_fee = 0.0
    parking_warning = None

    if bike and bike["status"] == "out_of_zone":
        relocation_fee = 5.00
        parking_warning = "Bike parked outside legal parking zone. Relocation fee applied."

    cost = round(ride_cost + relocation_fee, 2)

    if user["balance"] < cost:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    new_balance = round(user["balance"] - cost, 2)

    finished_ride = finish_ride(
        ride_id=active_ride["ride_id"],
        duration_min=duration_min,
        cost=cost,
    )

    update_user_balance(data.user_id, new_balance)
    update_bike_lock(active_ride["bike_id"], "locked")

    return {
        "message": "Ride ended",
        "ride": finished_ride,
        "ride_cost": ride_cost,
        "relocation_fee": relocation_fee,
        "parking_warning": parking_warning,
        "cost": cost,
        "new_balance": new_balance,
    }


@app.get("/api/orders/{user_id}")
async def get_user_orders(user_id: int):
    """Return ride history for the user."""
    user = fetch_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {"orders": fetch_rides_by_user(user_id)}


# 3. 页面路由
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
async def user_app(request: Request):
    return templates.TemplateResponse("user.html", {"request": request})


# 4. 管理员路由
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


# 5. 原来的车辆/违规/区域 API
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
