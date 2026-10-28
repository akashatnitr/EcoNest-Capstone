# Home I/O interactive test home

EcoNest exposes **Home I/O Interactive Test Home** as an isolated household.
It is safe to use without installing Home I/O: all visible sensor changes,
occupancy moves, and device controls remain in EcoNest's in-memory demo state.

## What is implemented now

- a Home I/O-shaped room/device inventory: entry, living room, kitchen,
  bedroom, bathroom, and garage;
- motion, door-contact, smoke, thermostat, lighting, and power states;
- manual sensor/device controls and resident placement;
- the existing EcoNest prompt, recommendation, action-audit, and digital-twin
  experiences.

The UI intentionally displays **"Home I/O bridge disconnected"**.  This avoids
mistaking the demo model for a live desktop simulation.

## Connecting a licensed Home I/O desktop instance

Home I/O's Python SDK uses `pythonnet` and the `EngineIO` library beside the
Windows desktop application.  Keep that SDK process on the machine running
Home I/O; do not put its DLLs or license keys in this repository or the
EcoNest web container.

The bridge should expose only a small authenticated local API to EcoNest:

```text
GET  /health
GET  /state                 -> normalized rooms, devices, and timestamp
POST /devices/{id}/state    -> bounded state: on|off|active|idle|normal
```

The bridge maps Home I/O memory-map addresses to EcoNest IDs such as
`living_motion`, `virtual_living_light`, and `living_thermostat`. It must
allow-list writable actuator IDs, reject unknown state values, and bind to a
private network interface.  EcoNest should then change the mode label only
after a successful health check, preserving the distinction between simulated
controls and any physical connected-home integration.

## Seeding the household

After the MySQL service is running, create the selectable household once:

```bash
poetry run python scripts/seed_home_io_test_home.py
```

Then choose **Home I/O Interactive Test Home** in the EcoNest household
switcher and open **Digital twin**.

## Phase 1 bridge is ready

The Windows-side implementation is at `scripts/home_io_desktop_bridge.py`.
On the Windows Home I/O machine, install 32-bit Python (to match Home I/O),
`pythonnet`, `fastapi`, and `uvicorn`. Use the EngineIO assembly bundled with
Connect I/O, then set an explicit, minimal map discovered with Connect I/O's
**Tags** panel:

```powershell
$env:PYTHONNET_RUNTIME = 'netfx'
$env:HOME_IO_SDK_PATH = 'C:\Program Files (x86)\Real Games\Connect IO'
$env:HOME_IO_BRIDGE_TOKEN = '<long-random-token>'
$env:HOME_IO_BRIDGE_MAP = '{
  "virtual_living_light": {"address": 0, "memory_type": "Output", "writable": true}
}'
py -3.11-32 -m uvicorn scripts.home_io_desktop_bridge:app --host 0.0.0.0 --port 8877
```

The room-A light mapping above was verified against the stock Home I/O house.
For other devices, set the device's floating mode icon to **blue** (External
Mode), then use Connect I/O's Tags panel and Properties to get its real address.
Do not enable `WriteActuatorsExternal` globally: it forces Home I/O to overwrite
external actuator values. Leave it `false` when a device is in External Mode.

Next, configure EcoNest with `HOME_IO_BRIDGE_ENABLED=true`,
`HOME_IO_BRIDGE_URL`, and the matching `HOME_IO_BRIDGE_TOKEN`. The bridge must
be reachable only over the private Tailnet; it should never be port-forwarded to
the public internet. The Digital Twin will report bridge health, and its
Living-room light control will dispatch to `virtual_living_light` when live.
