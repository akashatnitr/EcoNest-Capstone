# EcoNest — Smart Home Energy Monitoring & Automation

EcoNest is a capstone project that builds an intelligent smart home system focused on energy monitoring, anomaly detection, and AI-driven automation. It combines a Flask-based backend, real-time sensor polling, Home Assistant integration, and local LLM inference to provide actionable insights and alerts.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Repository Structure](#repository-structure)
3. [Quick Start](#quick-start)
4. [Backend API](#backend-api)
5. [Frontend / Sensor Polling](#frontend--sensor-polling)
6. [Machine Learning & AI](#machine-learning--ai)
7. [Home Assistant](#home-assistant)
8. [Database Schema](#database-schema)
9. [Hardware & Sensors](#hardware--sensors)
10. [Documentation](#documentation)
11. [Contributors](#contributors)

---

## Project Overview

EcoNest monitors energy consumption, motion, and sound across a home using smart plugs, breaker-level sensors, and GPIO-based microphones. Data is collected into a MySQL database, analyzed for anomalies, and processed by a local Mistral LLM (via Ollama) to generate efficiency recommendations and alerts. The system can send SMS notifications for high-severity events.

### Key Features

- **Real-time sensor polling** — Power, motion, and sound data from Home Assistant and Raspberry Pi GPIO
- **RESTful backend** — Flask API for rooms, devices, and sensor readings
- **Anomaly detection** — Power spikes, off-schedule usage, and security events
- **AI-powered inference** — Local LLM reasoning for alerts, efficiency tips, and scheduled checks
- **Home Assistant integration** — Automations for lighting, security, climate, and irrigation
- **SMS alerts** — Gmail SMTP-to-SMS gateway for critical notifications

---

## Repository Structure

```
EcoNest-Capstone/
├── medium home/                 # Main application code
│   ├── backend/
│   │   └── backend.py           # Flask REST API (rooms, devices, readings)
│   ├── frontend/
│   │   └── frontend.py          # Sensor poller: sound, motion, power → backend
│   ├── sensors/
│   │   ├── energy_sound_logger.py   # Raspberry Pi energy + sound logger
│   │   ├── sound_logger.py
│   │   ├── energy_test.py
│   │   └── logger.py
│   ├── tests/
│   │   ├── read_all_devices.py
│   │   ├── sound_test.py
│   │   └── test_mic.py
│   └── homeassistant/
│       ├── configuration.yaml
│       ├── automations.yaml
│       └── scenes.yaml
├── Machine_learning/            # AI/ML analytics & alerting
│   ├── requirements.txt
│   ├── updated_backend.py
│   ├── archives/
│   └── scripts/
│       ├── conn_check.py        # DB connectivity sanity check
│       ├── analytics.py         # Historical baseline computation
│       ├── trigger.py           # Real-time anomaly detector
│       ├── inference.py         # Mistral LLM inference daemon
│       └── demo.py              # Interactive showcase
├── homeassistant/               # Standalone HA configs
│   ├── lights_configuration.yaml
│   ├── security_configuration.yaml
│   ├── cc_configuration.yaml
│   ├── autoclosesingle_automation.yaml
│   ├── autoclosedouble_automation.yaml
│   ├── securityalert_automation.yaml
│   ├── soundmotionspike_automation.yaml
│   └── vegetablebeds_autowatering_automation.yaml
├── Documentation/
│   ├── Proposal/
│   ├── CDR/
│   ├── Hardware Manuals/
│   └── Weekly Reports/
└── database_schema.txt          # MySQL schema definition
```

---

## Quick Start

### Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.9+ | |
| MySQL | 8.0+ | Schema in `database_schema.txt` |
| Home Assistant | Any recent | Long-lived access token required |
| Ollama | Any | `mistral` model pulled |
| Gmail account | — | App password for SMS alerts |

### 1. Database Setup

```bash
mysql -u root -p < database_schema.txt
```

### 2. Environment Configuration

Create a `.env` file in the relevant directories:

```env
# MySQL
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=econest
DB_PORT=3306

# Home Assistant
HA_URL=http://localhost:8123
HA_TOKEN=your_long_lived_access_token

# Gmail SMTP (for SMS alerts)
SMS_GMAIL=your-gmail@gmail.com
SMS_GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
SMS_TO=5551234567@tmomail.net
```

### 3. Install Python Dependencies

```bash
cd Machine_learning
pip install -r requirements.txt

# Pull the Mistral model
ollama pull mistral
```

### 4. Start the Backend

```bash
cd "medium home/backend"
python backend.py
```

The API will be available at `http://127.0.0.1:5000`.

---

## Backend API

The Flask backend (`medium home/backend/backend.py`) provides REST endpoints for:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/rooms/add` | POST | Create a new room |
| `/rooms/get_id` | POST | Get room ID by name |
| `/rooms/list` | GET | List all rooms |
| `/devices/add` | POST | Register a device |
| `/devices/list/<room_id>` | GET | List devices in a room |
| `/devices/toggle` | POST | Toggle device on/off |
| `/devices/status` | GET | Get device active status |
| `/readings/add` | POST | Submit sensor readings (batch or single) |

### Example: Add a Reading

```bash
curl -X POST http://127.0.0.1:5000/readings/add \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": 1,
    "data": {"power": 120.5}
  }'
```

---

## Frontend / Sensor Polling

`medium home/frontend/frontend.py` runs as a daemon polling three sensor types:

- **Sound** — Microphone via `sounddevice`, detects spikes above a dB threshold
- **Motion** — Home Assistant binary sensor (`binary_sensor.hobeian_zg_204zl`)
- **Power** — Home Assistant entity states for Emporia Vue breakers and smart plugs

Data is POSTed in batches to the backend API every 30 seconds (power) or on state change (motion/sound spikes).

---

## Machine Learning & AI

Located in `Machine_learning/scripts/`. See `Machine_learning/scripts/README.md` for full details.

| Script | Purpose | Run Frequency |
|--------|---------|---------------|
| `conn_check.py` | Verify DB connectivity | On-demand |
| `analytics.py` | Compute 7-day baselines per room | Cron (hourly) |
| `trigger.py` | Detect anomalies in real time | Always-on daemon |
| `inference.py` | LLM classification & SMS alerts | Always-on daemon |
| `demo.py` | Interactive showcase of all scenarios | On-demand |

### Production Setup

```bash
# Terminal 1 — anomaly detector
python trigger.py

# Terminal 2 — inference daemon
python inference.py

# Cron — analytics refresh
0 * * * * /usr/bin/python3 /path/to/scripts/analytics.py
```

---

## Home Assistant

The `homeassistant/` directory contains YAML configurations for:

- **Lighting** — Scene-based control and automation
- **Security** — Motion alerts, auto-locking, sound-spike detection
- **Climate** — Thermostat scheduling
- **Irrigation** — Vegetable bed auto-watering based on soil moisture
- **Window Coverings** — Auto-close blinds based on time/temperature

Copy the relevant YAML snippets into your Home Assistant `configuration.yaml` or use the UI automation editor.

---

## Database Schema

The MySQL schema defines six tables:

- **`rooms`** — Room registry
- **`devices`** — Smart plugs, motion sensors, sound sensors
- **`sensor_readings`** — Raw time-series sensor data (JSON)
- **`home_snapshot`** — Current state + anomaly flags (updated by `trigger.py`)
- **`home_analytics`** — Historical baselines per room per hour (updated by `analytics.py`)
- **`device_profiles`** — Expected power ranges, active hours, thresholds

Full schema: [`database_schema.txt`](database_schema.txt)

---

## Hardware & Sensors

- **Emporia Vue Gen 3** — Whole-home energy monitoring (breaker-level)
- **TP-Link Kasa Smart Plugs** — Per-device energy monitoring
- **Raspberry Pi 4B** — Edge logger for GPIO sound sensor + smart plug polling
- **Mac Mini / Home Assistant Green** — Home Assistant host
- **Zigbee Motion Sensor** — Presence detection

Hardware manuals are in `Documentation/Hardware Manuals/`.

---

## Documentation

- **Proposal** — Initial project pitch and scope
- **CDR** — Critical Design Review presentation and report
- **Weekly Reports** — Progress tracking across the semester
- **Hardware Manuals** — Setup guides for Emporia Vue, Raspberry Pi, Kasa plugs, etc.

---

## Contributors

This project was developed as a senior capstone. See the proposal and weekly reports in `Documentation/` for contributor details.
