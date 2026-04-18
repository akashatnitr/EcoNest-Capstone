import asyncio
import sounddevice as sd
import numpy as np
import requests
import time
import threading
import os

from dotenv import load_dotenv
from kasa import SmartPlug

load_dotenv()

# ── Configuration ──────────────────────────────────────────────
HA_URL = os.getenv("HA_URL", "http://100.92.98.121:8123")
HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_TOKEN:
    raise ValueError("HA_TOKEN not found in .env file — please add it")

BACKEND_URL = "http://127.0.0.1:5000/readings/add"

# Sutton's Home
HOME_ID = 1

# Device IDs
KASA_DEVICE_IDS = {
    "Airfryer plug": 1,
    "Microwave plug": 2,
    "Washer plug": 3,
    "Bedroom Power strip": 4,
    "Living Room Setup": 5,
}

SOUND_DEVICE_ID = 28
MOTION_DEVICE_ID = 29

# Kasa devices
KASA_DEVICES = {
    "192.168.1.104": "Airfryer plug",
    "192.168.1.102": "Microwave plug",
    "192.168.1.226": "Washer plug",
    "192.168.1.16": "Bedroom Power strip",
    "192.168.1.55": "Living Room Setup",
}

# Motion sensor in Home Assistant
MOTION_ENTITY_ID = "binary_sensor.hobeian_zg_204zl"

# Intervals
SOUND_INTERVAL = 10       # seconds
POWER_INTERVAL = 60      # seconds
MOTION_INTERVAL = 0.5    # seconds

# Sound
SPIKE_THRESHOLD = 70

last_motion_state = None

HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}


# ── Helpers ────────────────────────────────────────────────────
def post_readings(payload):
    try:
        resp = requests.post(BACKEND_URL, json=payload, timeout=5)
        print(f"[POST] status={resp.status_code}")

        # Try to print backend response for debugging
        try:
            print(f"[POST] response={resp.json()}")
            return resp.json()
        except Exception:
            print(f"[POST] raw response={resp.text}")
            return None

    except requests.RequestException as e:
        print(f"[ERROR] Backend unreachable: {e}")
        return None


def get_ha_state(entity_id):
    try:
        resp = requests.get(
            f"{HA_URL}/api/states/{entity_id}",
            headers=HA_HEADERS,
            timeout=3
        )
        resp.raise_for_status()
        return resp.json().get("state")
    except requests.RequestException as e:
        print(f"[ERROR] HA unreachable for {entity_id}: {e}")
        return None


# ── Thread: Sound sensor ───────────────────────────────────────
def sound_loop():
    print("[Sound] Thread started")

    while True:
        try:
            recording = sd.rec(int(0.5 * 44100), samplerate=44100, channels=1)
            sd.wait()

            volume_norm = np.linalg.norm(recording) * 10
            db = round(float(20 * np.log10(volume_norm + 1e-6)), 2)
            spike = db > SPIKE_THRESHOLD

            payload = {
                "device_id": SOUND_DEVICE_ID,
                "data": {
                    "sound_level": db,
                    "spike": spike
                }
            }

            post_readings(payload)

            if spike:
                print(f"[Sound] SPIKE at {time.strftime('%H:%M:%S')} — {db} dB")
            else:
                print(f"[Sound] {db} dB — no spike")

        except Exception as e:
            print(f"[Sound] Error: {e}")

        time.sleep(SOUND_INTERVAL)


# ── Thread: Motion sensor ──────────────────────────────────────
def motion_loop():
    global last_motion_state
    print(f"[Motion] Thread started — polling {MOTION_ENTITY_ID} every {MOTION_INTERVAL}s")

    while True:
        try:
            state = get_ha_state(MOTION_ENTITY_ID)
            print(f"[Motion] Raw HA state: {state}")

            if state is not None and state != last_motion_state:
                last_motion_state = state
                detected = (state == "on")

                payload = {
                    "device_id": MOTION_DEVICE_ID,
                    "data": {
                        "motion": detected
                    }
                }

                post_readings(payload)

                if detected:
                    print(f"[Motion] MOTION DETECTED at {time.strftime('%H:%M:%S')}")
                else:
                    print(f"[Motion] Clear at {time.strftime('%H:%M:%S')}")

        except Exception as e:
            print(f"[Motion] Error: {e}")

        time.sleep(MOTION_INTERVAL)


# ── Async helper: Kasa poll ────────────────────────────────────
async def get_kasa_batch():
    batch = []

    for ip, name in KASA_DEVICES.items():
        try:
            plug = SmartPlug(ip)
            await plug.update()

            power = round(float(plug.emeter_realtime.power), 4)
            voltage = round(float(plug.emeter_realtime.voltage), 4)
            current = round(float(plug.emeter_realtime.current), 4)

            # total consumption can vary by python-kasa version / model
            total_kwh = 0.0
            try:
                total_kwh = round(float(plug.emeter_realtime.total), 4)
            except Exception:
                pass

            batch.append({
                "device_id": KASA_DEVICE_IDS[name],
                "data": {
                    "power": power,
                    "voltage": voltage,
                    "current": current,
                    "total_kwh": total_kwh
                }
            })

            print(f"[Power] {name}: {power} W | {voltage} V | {current} A | {total_kwh} kWh")

        except Exception as e:
            print(f"[Power] Error reading {name} ({ip}): {e}")

    return batch


# ── Thread: Power poller ───────────────────────────────────────
def power_loop():
    print(f"[Power] Thread started — polling every {POWER_INTERVAL}s")

    while True:
        try:
            batch = asyncio.run(get_kasa_batch())

            if batch:
                result = post_readings(batch)
                if result:
                    print(f"[Power] {result.get('message')} | errors: {result.get('errors')}")
                else:
                    print("[Power] Batch sent, but backend returned no JSON")
            else:
                print("[Power] No readings to send")

        except Exception as e:
            print(f"[Power] Error: {e}")

        time.sleep(POWER_INTERVAL)


# ── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== EcoNest Sensor Poller — Sutton's Home (home_id: 1) ===")

    threads = [
        threading.Thread(target=sound_loop, daemon=True, name="Sound"),
        threading.Thread(target=motion_loop, daemon=True, name="Motion"),
        threading.Thread(target=power_loop, daemon=True, name="Power"),
    ]

    for t in threads:
        t.start()

    while True:
        time.sleep(1)
