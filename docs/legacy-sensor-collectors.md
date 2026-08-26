# Legacy sensor collectors: concise compatibility note

`medium home/` is a prior standalone prototype, not the current application
architecture. It is retained because some files can still describe or produce
sensor readings.

| Area | What it is | Current relevance |
| --- | --- | --- |
| `sensors/logger.py` | Kasa power, microphone sound, and HA motion collector | Can post compatible readings to `ORCHESTRATOR_URL/readings/add`; audit it only if it is deployed. |
| `sensors/energy_sound_logger.py` and `sound_logger.py` | Raspberry Pi/GPIO or microphone collectors with CSV logging | May post to the orchestrator, but have device-specific IDs and hardware dependencies. |
| `backend/backend.py` | Separate Flask + direct-MySQL API | Retired; it bypasses FastAPI authentication, graph, agents, and audit behavior. |
| `frontend/frontend.py` | A duplicate sensor polling script, despite its name | Not a product frontend; its worker threads are commented out. |
| `homeassistant/` | Older HA REST sensors and one notification automation | Historical reference only unless its configuration is installed in the live HA instance. |

The old HA configuration references `/readings/latest/{device_id}` on port 5000.
That endpoint is not part of the current FastAPI readings API, so do not adopt
the YAML as a current integration without a compatibility review.

If a collector is still deployed, verify only its endpoint, service-account JWT,
device IDs, payload keys, and stored MySQL records. Do not revive the Flask API
or the old HA REST configuration as part of normal operation.
