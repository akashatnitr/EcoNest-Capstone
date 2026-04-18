import asyncio
from kasa import SmartPlug

IP = "192.168.1.104" 

async def main():
    plug = SmartPlug(IP)
    await plug.update()
    print(f"Alias: {plug.alias}")
    print("On:", plug.is_on)

    # Energy monitoring info (for energy-monitoring models)
    emeter = plug.emeter_realtime
    print("Power (W):", emeter.power)
    print("Voltage (V):", emeter.voltage)
    print("Current (A):", emeter.current)
    print("Total energy (kWh):", plug.emeter_today)

asyncio.run(main())
