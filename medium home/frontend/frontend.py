import sounddevice as sd
import numpy as np
import requests
import time
import threading
import os
from dotenv import load_dotenv


load_dotenv()


# ── Configuration ──────────────────────────────────────────────
HA_URL   = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN")


if not HA_TOKEN:
    raise ValueError("HA_TOKEN not found in .env file — please add it")


BACKEND_URL = "http://127.0.0.1:5000/readings/add"


# Device IDs (home_id: 3 — Professor's House)
SOUND_DEVICE_ID  = 10
MOTION_DEVICE_ID = 9


# Intervals
SOUND_INTERVAL  = 2    # seconds
POWER_INTERVAL  = 30   # seconds
MOTION_INTERVAL = 0.5  # seconds


# Sound
SPIKE_THRESHOLD = 20


# Power
VOLTAGE = 120.0


DEVICE_MAP = {
     # Breaker circuits
    "sensor.breaker_1_power_minute_average":    11,   # Breaker 1
    "sensor.breaker_2_power_minute_average":  12,   # Breaker 2
    "sensor.breaker_3_power_minute_average":  13,   # Breaker 3
    "sensor.breaker_4_power_minute_average":  14,  # Breaker 4
    "sensor.breaker_5_power_minute_average":  15,  # Breaker 5
    "sensor.breaker_6_power_minute_average":  16,  # Breaker 6
    "sensor.breaker_7_power_minute_average":  17,  # Breaker 7
    "sensor.breaker_8_power_minute_average":  18,  # Breaker 8
    "sensor.breaker_9_power_minute_average": 19,  # Breaker 9
    "sensor.breaker_10_power_minute_average": 20,  # Breaker 10
    "sensor.breaker_11_power_minute_average": 21,  # Breaker 11
    "sensor.breaker_12_power_minute_average": 22,  # Breaker 12
    "sensor.breaker_13_power_minute_average": 23,  # Breaker 13
    "sensor.breaker_14_power_minute_average": 24,  # Breaker 14
    "sensor.breaker_15_power_minute_average": 25,  # Breaker 15
    "sensor.breaker_16_power_minute_average": 26,  # Breaker 16
    "sensor.breaker_17_power_minute_average": 28,  # Breaker 17
    "sensor.balance_power_minute_average": 33,     # Balance


    # Emporia smart plugs
    "sensor.disposal_power_minute_average":      1,  # Emporia Plug 1
    "sensor.kitchen_gfci_2_power_minute_average":     2,  # Emporia Plug 2
    "sensor.sp7_power_minute_average":     7,  # Emporia Plug 3
    "sensor.sp5_power_minute_average": 5,  # Emporia Front Door
    "sensor.sp6_power_minute_average": 6,
    "sensor.sp8_power_minute_average": 8,
    "sensor.xbox_power_minute_average": 3,
    "sensor.3_power_minute_average": 4,
    "sensor.plug_91_power_minute_average": 32,
    "sensor.plug_92_power_minute_average": 30,
    "sensor.plug_94_power_minute_average": 31,
    "sensor.plug_93_power_minute_average": 29,
}
# ───────────────────────────────────────────────────────────────


HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}


last_motion_state = None


# ── Helpers ────────────────────────────────────────────────────
def post_readings(payload):
    try:
        resp = requests.post(BACKEND_URL, json=payload, timeout=5)
        return resp.json()
    except requests.RequestException as e:
        print(f"  [ERROR] Backend unreachable: {e}")
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
        print(f"  [ERROR] HA unreachable for {entity_id}: {e}")
        return None


def get_all_ha_states():
    try:
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=5)
        resp.raise_for_status()
        return {e["entity_id"]: e["state"] for e in resp.json()}
    except requests.RequestException as e:
        print(f"  [ERROR] Could not reach Home Assistant: {e}")
        return {}


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


            if spike:
                payload = {
                    "device_id": SOUND_DEVICE_ID,
                    "data": {"sound_level": db, "spike": spike}
                }
                post_readings(payload)
                print(f"[Sound] ⚠️  SPIKE at {time.strftime('%H:%M:%S')} — {db}dB")
            else:
                print(f"[Sound] {db}dB — no spike")


        except Exception as e:
            print(f"[Sound] Error: {e}")


        time.sleep(SOUND_INTERVAL)


# ── Thread: Motion sensor ──────────────────────────────────────
def motion_loop():
    global last_motion_state
    print("[Motion] Thread started — polling every 0.5s")
    while True:
        try:
            state = get_ha_state("binary_sensor.hobeian_zg_204zl")


            if state is not None and state != last_motion_state:
                last_motion_state = state
                detected = state == "on"


                payload = {
                    "device_id": MOTION_DEVICE_ID,
                    "data": {"motion": detected}
                }
                post_readings(payload)


                if detected:
                    print(f"[Motion] ⚠️  MOTION DETECTED at {time.strftime('%H:%M:%S')}")
                else:
                    print(f"[Motion] Clear at {time.strftime('%H:%M:%S')}")


        except Exception as e:
            print(f"[Motion] Error: {e}")


        time.sleep(MOTION_INTERVAL)


# ── Thread: Power poller ───────────────────────────────────────
def power_loop():
    print("[Power] Thread started — polling every 60s")
    while True:
        try:
            print(f"[Power] Fetching states at {time.strftime('%H:%M:%S')}...")
            states = get_all_ha_states()
            batch = []


            for entity_id, device_id in DEVICE_MAP.items():
                raw = states.get(entity_id)
                if raw is None:
                    print(f"[Power] SKIP {entity_id} — not found")
                    continue
                try:
                    power = round(float(raw), 4)
                except ValueError:
                    print(f"[Power] SKIP {entity_id} — bad value: {raw}")
                    continue


                batch.append({
                    "device_id": device_id,
                    "data": {
                        "power":   power
                    }
                })


            if batch:
                result = post_readings(batch)
                print(f"[Power] {result.get('message')} | errors: {result.get('errors')}")
            else:
                print("[Power] No readings to send.")


        except Exception as e:
            print(f"[Power] Error: {e}")


        time.sleep(POWER_INTERVAL)


# ── Main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== EcoNest Sensor Poller — Professor's House (home_id: 3) ===")


    threads = [
        #threading.Thread(target=sound_loop,  daemon=True, name="Sound"),
        #threading.Thread(target=motion_loop, daemon=True, name="Motion"),
        #threading.Thread(target=power_loop,  daemon=True, name="Power"),
    ]


    for t in threads:
        t.start()


    while True:
        time.sleep(1)

