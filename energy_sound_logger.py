import asyncio
import csv
import os
import time
from datetime import datetime

from kasa import SmartPlug
import RPi.GPIO as GPIO

# ====== CONFIG ======

# Smart plug devices
DEVICES = {
    "192.168.1.104": "AirFryer",
    "192.168.1.102": "Microwave",
    "192.168.1.226": "Washer",
    "192.168.1.16": "Bedroom Powerstrip",
    "192.168.1.55": "Living Room TV",
}

ENERGY_LOG_FILE = "energy_log.csv"
SOUND_LOG_FILE = "sound_log.csv"

INTERVAL_SECONDS = 30          # how often to log energy data
SOUND_PIN = 17                 # BCM GPIO number (physical pin 11)
SOUND_SENSOR_ID = "front_door_sensor"

SOUND_POLL_INTERVAL = 0.01     # seconds between GPIO polls (10 ms)
DEBOUNCE_SECONDS = 2.0         # minimum time between logged sound events

# =====================


async def send_to_backend(event: dict):
    """
    Placeholder for your future database/API call.

    Example event structures:
      - Energy:
          {
              "type": "energy",
              "timestamp": "...",
              "name": "...",
              "ip": "...",
              "power": ...,
              "voltage": ...,
              "current": ...,
              "total_kwh": ...
          }

      - Sound:
          {
              "type": "sound",
              "timestamp": "...",
              "sensor_id": "sound_sensor_1"
          }

    For now, this does nothing. Later you can:
      - Use aiohttp / httpx to POST to your backend
      - Or write to a message queue, etc.
    """
    # TODO: implement when you have an endpoint
    return


# ========= ENERGY LOGIC =========

async def read_device(ip, name):
    plug = SmartPlug(ip)
    await plug.update()
    emeter = plug.emeter_realtime
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "name": name,
        "power": emeter.power,
        "voltage": emeter.voltage,
        "current": emeter.current,
        "total_kwh": plug.emeter_today,
    }


async def energy_log_loop():
    # Create file and header if it doesn't exist
    file_exists = os.path.isfile(ENERGY_LOG_FILE)
    with open(ENERGY_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "name",
                "power",
                "voltage",
                "current",
                "total_kwh",
            ],
        )
        if not file_exists:
            writer.writeheader()

        while True:
            tasks = [
                read_device(ip, name)
                for ip, name in DEVICES.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            logged_count = 0  # how many rows we actually write this cycle

            for r in results:
                if isinstance(r, Exception):
                    # You can log/print r if you want to debug
                    continue

                # Skip zero-power readings so they don't go in the CSV
                if r["power"] == 0:
                    continue
                # (If you want to skip near-zero instead, use: if r["power"] < 1.0:)

                writer.writerow(r)
                f.flush()
                logged_count += 1

                # Optional future backend hook for energy:
                event = {
                    "type": "energy",
                    **r,
                }
                await send_to_backend(event)

            print(
                f"[ENERGY] Logged {logged_count} active devices at "
                f"{datetime.now().isoformat(timespec='seconds')}"
            )
            await asyncio.sleep(INTERVAL_SECONDS)


# ========= SOUND LOGIC =========

async def sound_log_loop():
    """
    Monitors the digital sound sensor on SOUND_PIN and logs
    debounced events to SOUND_LOG_FILE.
    """
    # Prepare CSV
    file_exists = os.path.isfile(SOUND_LOG_FILE)
    with open(SOUND_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "sensor_id",
            ],
        )
        if not file_exists:
            writer.writeheader()

        last_state = GPIO.input(SOUND_PIN)  # current level at start
        last_event_time = 0.0

        print("[SOUND] Monitoring started with debounce "
              f"{DEBOUNCE_SECONDS}s...")

        while True:
            now = time.time()
            state = GPIO.input(SOUND_PIN)  # 0 = quiet, 1 = sound

            # Rising edge + debounce:
            if (
                state == 1
                and last_state == 0
                and (now - last_event_time) >= DEBOUNCE_SECONDS
            ):
                ts_iso = datetime.now().isoformat(timespec="seconds")
                row = {
                    "timestamp": ts_iso,
                    "sensor_id": SOUND_SENSOR_ID,
                }
                writer.writerow(row)
                f.flush()

                print(f"[SOUND] Event at {ts_iso}")

                event = {
                    "type": "sound",
                    "timestamp": ts_iso,
                    "sensor_id": SOUND_SENSOR_ID,
                }
                await send_to_backend(event)

                last_event_time = now

            last_state = state
            await asyncio.sleep(SOUND_POLL_INTERVAL)


# ========= MAIN =========

async def main():
    # Set up GPIO once, shared by all tasks
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(SOUND_PIN, GPIO.IN)

    print("Starting combined energy + sound logger...")
    print("  - Energy interval:", INTERVAL_SECONDS, "seconds")
    print("  - Sound debounce:", DEBOUNCE_SECONDS, "seconds")

    try:
        await asyncio.gather(
            energy_log_loop(),
            sound_log_loop(),
        )
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping logger...")
