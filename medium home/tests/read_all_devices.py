import asyncio
from kasa import SmartPlug
from datetime import datetime

# 👇 Put your device IPs & names here
DEVICES = {
    "192.168.1.104": "AirFryer",
    "192.168.1.102": "Microwave",
    "192.168.1.226": "Dishwasher",
    "192.168.1.16": "Bedroom PowerStrip",
    "192.168.1.55": "Living Room Setup",
}

async def read_device(ip, name):
    plug = SmartPlug(ip)
    await plug.update()
    emeter = plug.emeter_realtime
    return {
        "name": name,
        "ip": ip,
        "power": emeter.power,
        "voltage": emeter.voltage,
        "current": emeter.current,
        "total_kwh": plug.emeter_today,
    }

async def main():
    now = datetime.now().isoformat(timespec="seconds")
    tasks = [
        read_device(ip, name)
        for ip, name in DEVICES.items()
    ]
    results = await asyncio.gather(*tasks)

    print(f"Timestamp: {now}")
    for r in results:
        print(
            f"{r['name']} ({r['ip']}): "
            f"{r['power']:.2f} W, {r['voltage']:.1f} V, "
            f"{r['current']:.3f} A, {r['total_kwh']:.3f} kWh today"
        )

asyncio.run(main())
