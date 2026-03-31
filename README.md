# MQTT_bike_shareing_system 
coursework

This project is developed as part of the **C115 - Connected Devices** module.  
It implements a **prototype of a smart shared-bike system** using MQTT, backend processing, and real-time frontend visualization.

Unlike the original single-bike tracker demo, this version has been **refined and extended** into a more realistic system architecture.

---

##  Project Overview

The system simulates a **shared bike environment**, where multiple bikes can send real-time data through MQTT, and the backend processes this data and updates the frontend dynamically.

### Key Features

- Real-time bike tracking using MQTT
- Multi-bike support (via `bike_id`)
- Backend state management for multiple bikes
- Zone-based validation (safe / out_of_zone)
- Real-time frontend updates using Socket.IO
- Interactive map visualization

---

##  System Architecture

The system consists of three main components:

### 1. MQTT Layer
- Handles communication between simulated bikes and backend
- Supports structured topics (e.g. `bike/{bike_id}/gps`)
- Sends JSON-based payloads including location and status

### 2. Backend (FastAPI)
- Receives MQTT messages
- Maintains dynamic bike state (multi-bike structure)
- Applies rule-based logic (zone validation)
- Pushes updates to frontend via Socket.IO

### 3. Frontend (Map Interface)
- Displays bike positions in real time
- Updates dynamically based on backend events
- Visualizes bike status and movement

---

##  Technologies Used

- **MQTT** – Real-time communication protocol  
- **Python / FastAPI** – Backend server  
- **Paho-MQTT** – MQTT client  
- **Socket.IO** – Real-time frontend updates  
- **HTML / CSS / JavaScript (Leaflet.js)** – Map visualization  

---

##  Installation & Usage

### Requirements

- Python 3.10+
- Web browser (Chrome recommended)

---

### 1. Install dependencies
pip install -r requirements.txt

### 2. Start backend server
uvicorn main:socket_app --reload

### 3. Start MQTT publisher (simulated bike)
python publisher.py

### 4. Open in browser
http://127.0.0.1:8000
