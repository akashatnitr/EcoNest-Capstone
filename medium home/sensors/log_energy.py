import asyncio
import csv
import os
import time
from datetime import datetime
from kasa import SmartPlug

# 👇 Same device dict as before
DEVICES = {
    "192.168.1.104": "AirFryer",
    "192.168.1.102": "Microwave",
    "192.168.1.226": "Washer",
    "192.168.1.16": "Bedroom Powerstrip",
    "192.168.1.55": "Living Room TV",
}

LOG_FILE = "energy_log.csv"
INTERVAL_SECONDS = 10  # how often to log

async def read_device(ip, name):
    plug = SmartPlug(ip)
    await plug.update()
    emeter = plug.emeter_realtime
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "name": name,
        "ip": ip,
        "power": emeter.power,
        "voltage": emeter.voltage,
        "current": emeter.current,
        "total_kwh": plug.emeter_today,
    }

async def log_loop():
    # Create file and header if it doesn't exist
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "name",
                "ip",
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

            for r in results:
                if isinstance(r, Exception):
                    # you could print/log the error if you want
                    continue
                writer.writerow(r)
            f.flush()
            print(f"Logged {len(DEVICES)} devices at {datetime.now().isoformat(timespec='seconds')}")
            await asyncio.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    asyncio.run(log_loop())
