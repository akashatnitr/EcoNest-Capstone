"""In-memory, safe runtime controls for the City Apartment demonstration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from orchestrator.config import get_settings
from orchestrator.core.home_io_bridge import (
    home_io_bridge_points,
    pulse_home_io_bridge_point,
    set_home_io_bridge_point,
)
from orchestrator.core.simulated_households import (
    CASAS_ARUBA_NAME,
    CITY_APARTMENT_NAME,
    CN_OBEE_NAME,
    HOME_IO_NAME,
    casas_aruba_state,
    city_apartment_state,
    simulated_household_state,
)

SimulationAction = Literal[
    "resident_arrives",
    "resident_leaves",
    "heat_wave",
    "advance_ten_minutes",
    "reset",
    "living_light_on",
    "living_light_off",
    "kitchen_light_on",
    "kitchen_light_off",
]

_states: dict[int, dict[str, Any]] = {}
_lock = asyncio.Lock()
_SIMULATION_TIMEZONE = ZoneInfo("America/Chicago")

_CASAS_LIVE_STEPS: tuple[dict[str, Any], ...] = (
    {
        "clock": "05:30 AM", "activity": "Sleeping", "rooms": ("bedroom_1",),
        "sensors": ("m002", "m003", "m007"),
        "event": "Dataset-calibrated sleep activity continues in Bedroom 1.",
    },
    {
        "clock": "05:40 AM", "activity": "Bed_to_Toilet", "rooms": ("bedroom_1", "bathroom_2"),
        "sensors": ("m003", "m004", "m005", "m007"),
        "event": "Dataset-calibrated transition from Bedroom 1 to Bathroom 2.",
    },
    {
        "clock": "05:50 AM", "activity": "Personal_Hygiene", "rooms": ("bathroom_1",),
        "sensors": ("m029", "m031"),
        "event": "Dataset-calibrated morning hygiene activity inferred in Bathroom 1.",
    },
    {
        "clock": "06:00 AM", "activity": "Meal_Preparation", "rooms": ("kitchen",),
        "sensors": ("m015", "m016", "m017", "m018", "m019"),
        "event": "Dataset-calibrated meal-preparation pattern inferred in Kitchen.",
    },
    {
        "clock": "06:10 AM", "activity": "Eating", "rooms": ("dining",),
        "sensors": ("m014",),
        "event": "Dataset-calibrated eating activity inferred in Dining.",
    },
    {
        "clock": "06:20 AM", "activity": "Relax", "rooms": ("living_room",),
        "sensors": ("m009", "m013", "m020"),
        "event": "Dataset-calibrated relaxation activity inferred in Living room.",
    },
    {
        "clock": "06:30 AM", "activity": "Work", "rooms": ("office",),
        "sensors": ("m026", "m027", "m028"),
        "event": "Dataset-calibrated work activity inferred in Office.",
    },
    {
        "clock": "06:40 AM", "activity": "Leave_Home", "rooms": ("garage",),
        "sensors": ("m030", "d004"),
        "event": "Dataset-calibrated leave-home transition inferred at Garage entry.",
    },
)


async def city_apartment_runtime_state(household_id: int) -> dict[str, Any]:
    """Return an isolated snapshot of one simulated apartment's current state."""
    async with _lock:
        return deepcopy(_states.setdefault(household_id, city_apartment_state()))


async def simulated_runtime_state(household_id: int, household_name: str) -> dict[str, Any]:
    """Return one simulated household's state, including allow-listed Home I/O points."""
    initial = simulated_household_state(household_name)
    if initial is None:
        raise ValueError(f"Unsupported simulated household: {household_name}")
    async with _lock:
        if household_id not in _states:
            _states[household_id] = _new_simulated_state(household_name, initial)
        if household_name == CN_OBEE_NAME:
            _synchronize_cn_obee_clock(_states[household_id])
        if household_name == HOME_IO_NAME:
            _synchronize_home_io_clock(_states[household_id])
        snapshot = deepcopy(_states[household_id])
    if household_name != HOME_IO_NAME:
        return snapshot

    points = await home_io_bridge_points()
    if points is None:
        return snapshot
    async with _lock:
        state = _states[household_id]
        _apply_live_home_io_points(state, points)
        return deepcopy(state)


async def apply_city_apartment_action(
    household_id: int, action: SimulationAction
) -> dict[str, Any]:
    """Apply a bounded, safe demonstration event to one simulated apartment."""
    async with _lock:
        if action == "reset":
            _states[household_id] = city_apartment_state()
            return deepcopy(_states[household_id])
        state = _states.setdefault(household_id, city_apartment_state())
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        if action == "resident_arrives":
            state["summary"]["occupancy"] = "Home"
            rooms["living_room"]["occupied"] = True
            devices["living_dimmer"]["state"] = "on"
            devices["living_dimmer"]["detail"] = "On · motion inferred"
            _record(state, "Resident arrived; Living room activity is now inferred.")
        elif action == "resident_leaves":
            state["summary"]["occupancy"] = "Away"
            for room in rooms.values():
                room["occupied"] = False
            for device_id in ("living_dimmer", "kitchen_dimmer"):
                devices[device_id]["state"] = "off"
                devices[device_id]["detail"] = "Off · no activity"
            state["summary"]["active_rooms"] = 0
            state["summary"]["power_w"] = 96
            _record(state, "Resident left; activity and non-essential lights were cleared.")
        elif action == "heat_wave":
            for room in rooms.values():
                room["temperature_f"] += 3
            state["summary"]["indoor_temperature_f"] += 3
            devices["living_thermostat"]["detail"] = (
                f"{rooms['living_room']['temperature_f']}°F · set to 73°F"
            )
            _record(state, "Outdoor heat raised the simulated indoor temperature by 3°F.")
        elif action == "advance_ten_minutes":
            state["summary"]["active_rooms"] = sum(
                1 for room in rooms.values() if room["occupied"]
            )
            _record(state, "Advanced simulated time by 10 minutes.")
        return deepcopy(state)


async def apply_simulation_action(
    household_id: int, household_name: str, action: SimulationAction
) -> dict[str, Any]:
    """Apply a bounded event to either supported simulated household."""
    if household_name == CITY_APARTMENT_NAME:
        return await apply_city_apartment_action(household_id, action)
    if household_name == CN_OBEE_NAME:
        return await apply_cn_obee_action(household_id, action)
    initial = simulated_household_state(household_name)
    if initial is None:
        raise ValueError(f"Unsupported simulated household: {household_name}")
    async with _lock:
        if action == "reset":
            _states[household_id] = _new_simulated_state(household_name, initial)
            return deepcopy(_states[household_id])
        state = _states.setdefault(
            household_id, _new_simulated_state(household_name, initial)
        )
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        overlays = state.setdefault("visual_overlays", {})
        if action in {"living_light_on", "living_light_off", "kitchen_light_on", "kitchen_light_off"}:
            device_id = "virtual_living_light" if action.startswith("living") else "virtual_kitchen_light"
            is_on = action.endswith("_on")
            devices[device_id]["state"] = "on" if is_on else "off"
            devices[device_id]["detail"] = (
                "On · EcoNest simulation actuator" if is_on else "Off · EcoNest simulation actuator"
            )
            on_lights = sum(
                1 for device in devices.values()
                if device.get("id", "").startswith("virtual_") and device.get("kind") == "light"
                and device.get("state") == "on"
            )
            state["summary"]["power_w"] = on_lights * 9
            devices["virtual_energy_meter"]["detail"] = (
                f"{state['summary']['power_w']} W · simulated lighting load"
            )
            _record(state, f"Virtual {device_id.removeprefix('virtual_').replace('_', ' ')} turned {'on' if is_on else 'off'}.")
        elif action == "resident_leaves":
            state["summary"]["occupancy"] = "Away"
            state["summary"]["active_rooms"] = 0
            for room in rooms.values():
                room["occupied"] = False
            overlays["resident_arriving"] = False
            overlays["resident_visible"] = False
            overlays["resident_room"] = None
            overlays["car_present"] = False
            _record(state, "Replay branch: no motion after the resident leaves.")
        elif action == "resident_arrives":
            state["summary"]["occupancy"] = "Home"
            state["summary"]["active_rooms"] = 1
            rooms["living_room"]["occupied"] = True
            overlays["resident_arriving"] = True
            overlays["resident_visible"] = True
            overlays["resident_room"] = "front_entry"
            overlays["car_present"] = True
            _record(state, "Replay branch: resident arrival inferred from entry activity.")
        elif action == "heat_wave":
            for room in rooms.values():
                room["temperature_f"] += 3
            state["summary"]["indoor_temperature_f"] += 3
            _record(state, "Counterfactual heat event added to the recorded sensor replay.")
        else:
            live = state.get("live_simulation", {})
            simulated_at = datetime.fromisoformat(str(live["simulated_at"]))
            simulated_at += timedelta(minutes=10)
            _apply_casas_live_step(state, _casas_step_for_time(simulated_at), simulated_at)
        return deepcopy(state)


async def apply_casas_manual_override(
    household_id: int,
    *,
    device_id: str | None = None,
    device_state: str | None = None,
    resident_room: str | None = None,
) -> dict[str, Any]:
    """Apply a bounded, manual override to the CASAS simulation only."""
    async with _lock:
        state = _states.setdefault(household_id, _new_simulated_state(CASAS_ARUBA_NAME, casas_aruba_state()))
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        overlays = state.setdefault("visual_overlays", {})
        if device_id is not None:
            if device_id not in devices or device_state not in {
                "normal",
                "active",
                "idle",
                "on",
                "off",
            }:
                raise ValueError("Unsupported simulated device override")
            device = devices[device_id]
            device["state"] = device_state
            device["detail"] = f"{device_state.replace('_', ' ').title()} · manual simulation override"
            if device["kind"] == "motion" and device_state == "active":
                room_id = str(device["room"])
                for room in rooms.values():
                    room["occupied"] = room["id"] == room_id
                state["summary"]["occupancy"] = "Home"
                state["summary"]["active_rooms"] = 1
                overlays.update({
                    "resident_visible": True,
                    "resident_arriving": False,
                    "resident_room": room_id,
                    "car_present": True,
                })
            _record(state, f"Manual override: {device['label']} set to {device_state}.")
        if resident_room is not None:
            if resident_room == "away":
                for room in rooms.values():
                    room["occupied"] = False
                state["summary"]["occupancy"] = "Away"
                state["summary"]["active_rooms"] = 0
                overlays.update({"resident_visible": False, "resident_arriving": False, "resident_room": None, "car_present": False})
                _record(state, "Manual override: resident set away.")
            else:
                if resident_room not in rooms:
                    raise ValueError("Unsupported simulated room")
                for room in rooms.values():
                    room["occupied"] = room["id"] == resident_room
                for device in devices.values():
                    if device["kind"] == "motion":
                        device["state"] = "active" if device["room"] == resident_room else "idle"
                state["summary"]["occupancy"] = "Home"
                state["summary"]["active_rooms"] = 1
                overlays.update({"resident_visible": True, "resident_arriving": False, "resident_room": resident_room, "car_present": True})
                _record(state, f"Manual override: resident placed in {rooms[resident_room]['name']}.")
        return deepcopy(state)


async def apply_cn_obee_manual_override(
    household_id: int,
    *,
    device_id: str | None = None,
    device_state: str | None = None,
    resident_room: str | None = None,
) -> dict[str, Any]:
    """Apply a bounded manual override to the CN-OBEE simulation only.

    CN-OBEE provides room-presence signals rather than person tracking.  The
    resident marker is therefore an explicit EcoNest simulation overlay: an
    active room-occupancy sensor places it in that sensor's documented room.
    """
    async with _lock:
        state = _states.setdefault(
            household_id, simulated_household_state(CN_OBEE_NAME) or {}
        )
        _synchronize_cn_obee_clock(state)
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        overlays = state.setdefault("visual_overlays", {})
        if device_id is not None:
            if device_id not in devices or device_state not in {
                "normal",
                "active",
                "idle",
                "on",
                "off",
            }:
                raise ValueError("Unsupported simulated device override")
            device = devices[device_id]
            device["state"] = device_state
            device["detail"] = (
                f"{device_state.replace('_', ' ').title()} · manual simulation override"
            )
            if device["kind"] == "motion" and device_state == "active":
                room_id = str(device["room"])
                for room in rooms.values():
                    room["occupied"] = room["id"] == room_id
                state["summary"]["occupancy"] = "Home"
                state["summary"]["active_rooms"] = 1
                overlays.update({"resident_visible": True, "resident_room": room_id})
            _record(state, f"Manual override: {device['label']} set to {device_state}.")
        if resident_room is not None:
            if resident_room == "away":
                for room in rooms.values():
                    room["occupied"] = False
                for device in devices.values():
                    if device["kind"] == "motion":
                        device["state"] = "idle"
                state["summary"]["occupancy"] = "Away"
                state["summary"]["active_rooms"] = 0
                overlays.update({"resident_visible": False, "resident_room": None})
                _record(state, "Manual override: resident set away.")
            else:
                if resident_room not in rooms:
                    raise ValueError("Unsupported simulated room")
                for room in rooms.values():
                    room["occupied"] = room["id"] == resident_room
                for device in devices.values():
                    if device["kind"] == "motion":
                        device["state"] = (
                            "active" if device["room"] == resident_room else "idle"
                        )
                state["summary"]["occupancy"] = "Home"
                state["summary"]["active_rooms"] = 1
                overlays.update({"resident_visible": True, "resident_room": resident_room})
                _record(
                    state,
                    f"Manual override: resident placed in {rooms[resident_room]['name']}.",
                )
        return deepcopy(state)


async def apply_dataset_manual_override(
    household_id: int,
    household_name: str,
    *,
    device_id: str | None = None,
    device_state: str | None = None,
    resident_room: str | None = None,
) -> dict[str, Any]:
    """Dispatch a manual override only to a supported dataset simulation."""
    if household_name == CASAS_ARUBA_NAME:
        return await apply_casas_manual_override(
            household_id,
            device_id=device_id,
            device_state=device_state,
            resident_room=resident_room,
        )
    if household_name == CN_OBEE_NAME:
        return await apply_cn_obee_manual_override(
            household_id,
            device_id=device_id,
            device_state=device_state,
            resident_room=resident_room,
        )
    if household_name == HOME_IO_NAME:
        return await apply_home_io_manual_override(
            household_id,
            device_id=device_id,
            device_state=device_state,
            resident_room=resident_room,
        )
    raise ValueError("Manual overrides are unavailable for this simulated household")


async def apply_home_io_manual_override(
    household_id: int,
    *,
    device_id: str | None = None,
    device_state: str | None = None,
    resident_room: str | None = None,
) -> dict[str, Any]:
    """Apply a Home I/O demo control, using the live bridge when configured."""
    settings = get_settings()
    async with _lock:
        state = _states.setdefault(
            household_id, simulated_household_state(HOME_IO_NAME) or {}
        )
        _synchronize_home_io_clock(state)
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        if device_id is not None:
            if device_id not in devices or device_state not in {"normal", "active", "idle", "on", "off", "trigger"}:
                raise ValueError("Unsupported Home I/O control")
            device = devices[device_id]
            if device.get("momentary"):
                if device_state != "trigger":
                    raise ValueError("This Home I/O action must be triggered, not left on")
                applied = await pulse_home_io_bridge_point(device_id)
                if not applied:
                    raise ValueError("Home I/O did not accept the requested action")
                device["state"] = "active"
                device["detail"] = "Triggered · Live Home I/O action"
                _record(state, f"Home I/O: {device['label']} triggered in zone {device['room'].upper()}.")
            elif device.get("live_writable") and device_state in {"on", "off"}:
                applied = await set_home_io_bridge_point(device_id, device_state == "on")
                if applied is None:
                    if settings.HOME_IO_BRIDGE_ENABLED:
                        raise ValueError("Home I/O bridge is unavailable; the live simulator was not changed")
                elif applied != (device_state == "on"):
                    raise ValueError("Home I/O did not accept the requested light state")
                device["state"] = device_state
                device["detail"] = f"{device_state.title()} · Live Home I/O actuator"
                _record(state, f"Home I/O: {device['label']} set to {device_state} in zone {device['room'].upper()}.")
            elif not device.get("live_writable"):
                raise ValueError("This is a read-only Home I/O sensor")
            else:
                raise ValueError("Choose on or off for this Home I/O actuator")
            if device["kind"] == "motion" and device_state == "active":
                resident_room = str(device["room"])
        if resident_room is not None:
            if resident_room == "away":
                for room in rooms.values():
                    room["occupied"] = False
                for device in devices.values():
                    if device["kind"] == "motion":
                        device["state"] = "idle"
                state["summary"].update({"occupancy": "Away", "active_rooms": 0})
                _record(state, "Home I/O demo: resident set away.")
            else:
                if resident_room not in rooms:
                    raise ValueError("Unsupported Home I/O room")
                for room in rooms.values():
                    room["occupied"] = room["id"] == resident_room
                for device in devices.values():
                    if device["kind"] == "motion":
                        device["state"] = "active" if device["room"] == resident_room else "idle"
                state["summary"].update({"occupancy": "Home", "active_rooms": 1})
                _record(state, f"Home I/O demo: resident placed in {rooms[resident_room]['name']}.")
        return deepcopy(state)


async def apply_cn_obee_action(
    household_id: int, action: SimulationAction
) -> dict[str, Any]:
    """Apply safe counterfactual controls over a recorded CN-OBEE snapshot."""
    async with _lock:
        if action == "reset":
            _states[household_id] = simulated_household_state(CN_OBEE_NAME) or {}
            _synchronize_cn_obee_clock(_states[household_id])
            _record(_states[household_id], "Reset to the recorded CN-OBEE snapshot.")
            return deepcopy(_states[household_id])
        state = _states.setdefault(
            household_id, simulated_household_state(CN_OBEE_NAME) or {}
        )
        _synchronize_cn_obee_clock(state)
        rooms = {str(room["id"]): room for room in state["rooms"]}
        devices = {str(device["id"]): device for device in state["devices"]}
        overlays = state.setdefault("visual_overlays", {})
        if action in {
            "living_light_on", "living_light_off", "kitchen_light_on", "kitchen_light_off"
        }:
            device_id = (
                "virtual_living_light" if action.startswith("living")
                else "virtual_kitchen_light"
            )
            is_on = action.endswith("_on")
            devices[device_id]["state"] = "on" if is_on else "off"
            devices[device_id]["detail"] = (
                "On · EcoNest counterfactual control"
                if is_on else "Off · EcoNest counterfactual control"
            )
            _record(
                state,
                f"Counterfactual virtual {device_id.removeprefix('virtual_').replace('_', ' ')} "
                f"turned {'on' if is_on else 'off'}; source readings remain unchanged.",
            )
        elif action == "resident_leaves":
            state["summary"]["occupancy"] = "Away"
            state["summary"]["active_rooms"] = 0
            for room in rooms.values():
                room["occupied"] = False
                devices[f"{room['id']}_occupancy"]["detail"] = "Unoccupied · counterfactual"
            overlays.update({"resident_visible": False, "resident_room": None})
            _record(state, "Counterfactual away state applied; recorded source rows were not changed.")
        elif action == "resident_arrives":
            state["summary"]["occupancy"] = "Home"
            state["summary"]["active_rooms"] = 2
            rooms["cloakroom"]["occupied"] = True
            rooms["living_room"]["occupied"] = True
            devices["cloakroom_occupancy"]["detail"] = "Occupied · counterfactual arrival"
            devices["living_room_occupancy"]["detail"] = "Occupied · counterfactual arrival"
            overlays.update({"resident_visible": True, "resident_room": "cloakroom"})
            _record(state, "Counterfactual arrival applied in the Cloakroom and Living room.")
        elif action == "heat_wave":
            for room in rooms.values():
                room["temperature_f"] += 3
                temperature_c = (room["temperature_f"] - 32) * 5 / 9
                devices[f"{room['id']}_temperature"]["detail"] = (
                    f"{temperature_c:.1f}°C / {room['temperature_f']:.1f}°F "
                    "· counterfactual heat adjustment"
                )
            state["summary"]["indoor_temperature_f"] += 3
            _record(state, "Counterfactual heat event raised all room readings by 3°F.")
        else:
            _record(state, "Refreshed the live-paced CN-OBEE household clock.")
        return deepcopy(state)


def _synchronize_cn_obee_clock(state: dict[str, Any]) -> None:
    """Keep the CN-OBEE simulation clock aligned with EcoNest's local wall clock.

    The displayed 2026 time is deliberately separate from the retained CN-OBEE
    source timestamp: room readings remain calibrated to the public record until
    a complete CSV replay is imported.
    """
    now = datetime.now(_SIMULATION_TIMEZONE).replace(second=0, microsecond=0)
    live = state.setdefault("live_simulation", {})
    live["clock"] = now.strftime("%b %-d, %Y · %-I:%M %p %Z")
    live["simulated_at"] = now.isoformat()
    live["mode"] = "Live-paced 2026 CN-OBEE simulation"
    live["source_replay_time"] = "Recorded CN-OBEE baseline retained in provenance"
    live["activity"] = _cn_obee_time_context(now.hour)


def _synchronize_home_io_clock(state: dict[str, Any]) -> None:
    """Keep the demo household visibly tied to the local evaluation clock."""
    now = datetime.now(_SIMULATION_TIMEZONE).replace(second=0, microsecond=0)
    live = state.setdefault("live_simulation", {})
    live["clock"] = now.strftime("%b %-d, %Y · %-I:%M %p %Z")
    live["simulated_at"] = now.isoformat()
    live["mode"] = "EcoNest demo mode · Home I/O bridge disconnected"


def _apply_live_home_io_points(state: dict[str, Any], points: dict[str, bool]) -> None:
    """Merge only allow-listed desktop-simulator points into the visual twin."""
    devices = {str(device["id"]): device for device in state["devices"]}
    changed: list[str] = []
    for device_id, value in points.items():
        device = devices.get(device_id)
        if device is None:
            continue
        if device.get("live_writable"):
            next_state = "on" if value else "off"
            if not device.get("momentary") and device.get("state") != next_state:
                changed.append(f"{device['label']} is now {next_state} in live Home I/O.")
            if not device.get("momentary"):
                device["state"] = next_state
                device["detail"] = f"{next_state.title()} · Live Home I/O actuator"
        else:
            if device["kind"] == "motion":
                device["state"] = "active" if value else "idle"
                device["detail"] = ("Motion detected" if value else "No motion") + " · Live Home I/O sensor"
            elif device["kind"] == "smoke":
                device["state"] = "active" if value else "normal"
                device["detail"] = ("Smoke detected" if value else "Normal") + " · Live Home I/O sensor"
            else:
                device["state"] = "active" if value else "idle"
                device["detail"] = ("Active" if value else "Idle") + " · Live Home I/O sensor"
    live = state.setdefault("live_simulation", {})
    rooms = {str(room["id"]): room for room in state["rooms"]}
    active_motion_rooms = {
        str(device["room"])
        for device in devices.values()
        if device.get("kind") == "motion" and device.get("state") == "active"
    }
    for room_id, room in rooms.items():
        room["occupied"] = room_id in active_motion_rooms
    state["summary"].update({
        "occupancy": "Home" if active_motion_rooms else "No motion detected",
        "active_rooms": len(active_motion_rooms),
    })
    live["mode"] = "Live Home I/O desktop bridge"
    state["updated_label"] = "Live Home I/O state · synced from the Windows simulator"
    if changed:
        for event in changed:
            _record(state, event)


def _cn_obee_time_context(hour: int) -> str:
    """Provide an explicit EcoNest time-of-day context for an unlabeled dataset."""
    if hour < 6 or hour >= 22:
        return "Night-time at home"
    if hour < 9:
        return "Morning at home"
    if hour < 17:
        return "Daytime at home"
    return "Evening at home"


def _new_simulated_state(household_name: str, initial: dict[str, Any]) -> dict[str, Any]:
    """Create a present-day synthetic state for a supported simulated household."""
    state = deepcopy(initial)
    if household_name == CASAS_ARUBA_NAME:
        simulated_at = datetime.now(_SIMULATION_TIMEZONE).replace(second=0, microsecond=0)
        _apply_casas_live_step(state, _casas_step_for_time(simulated_at), simulated_at)
        _record(
            state,
            "Synthetic 2026 continuation initialized from CASAS activity and sensor patterns.",
        )
    elif household_name == HOME_IO_NAME:
        _synchronize_home_io_clock(state)
    return state


def _casas_step_for_time(simulated_at: datetime) -> int:
    """Choose a CASAS-calibrated routine state from local time of day."""
    hour = simulated_at.hour
    if hour < 6 or hour >= 22:
        return 0  # Sleeping
    if hour == 6:
        return 2  # Personal hygiene
    if hour == 7:
        return 3  # Meal preparation
    if hour == 8:
        return 4  # Eating
    if 9 <= hour < 17:
        return 6  # Work
    if hour == 17:
        return 7  # Leave/return transition
    if hour == 18:
        return 3
    if hour == 19:
        return 4
    return 5  # Relax


def _apply_casas_live_step(
    state: dict[str, Any], step_index: int, simulated_at: datetime
) -> None:
    """Advance the dataset-calibrated household through an activity pattern."""
    step = _CASAS_LIVE_STEPS[step_index]
    active_rooms = set(step["rooms"])
    active_sensors = set(step["sensors"])
    indoor_temperature_f = _synthetic_indoor_temperature_f(simulated_at)
    for room in state["rooms"]:
        room["occupied"] = room["id"] in active_rooms
        room["temperature_f"] = indoor_temperature_f
    for device in state["devices"]:
        if device.get("virtual") or device["kind"] != "motion":
            continue
        sensor_id = str(device["id"])
        device["state"] = "active" if sensor_id in active_sensors else "idle"
        device["detail"] = (
            f"ON · activity for {step['activity']}" if sensor_id in active_sensors
            else "No trigger in the current simulated interval"
        )
    occupancy = "Away" if step["activity"] == "Leave_Home" else "Home"
    overlays = state.setdefault("visual_overlays", {})
    active_motion_room = next(
        (
            str(device["room"])
            for device in state["devices"]
            if device["kind"] == "motion" and device["state"] == "active"
        ),
        next(iter(sorted(active_rooms)), None),
    )
    overlays["resident_visible"] = occupancy == "Home"
    overlays["resident_arriving"] = False
    overlays["resident_room"] = active_motion_room if occupancy == "Home" else None
    overlays["car_present"] = occupancy == "Home"
    state["summary"]["occupancy"] = occupancy
    state["summary"]["active_rooms"] = len(active_rooms) if occupancy == "Home" else 0
    state["summary"]["indoor_temperature_f"] = indoor_temperature_f
    state["live_simulation"] = {
        "clock": simulated_at.strftime("%b %-d, %Y · %-I:%M %p %Z"),
        "simulated_at": simulated_at.isoformat(),
        "activity": step["activity"],
        "mode": "Synthetic 2026 live simulation · CASAS-calibrated",
        "step": step_index,
        "provenance": "Synthetic continuation; behavioral parameters are calibrated from CASAS Aruba (2010-11-04 to 2011-06-11).",
    }
    _record(state, step["event"])


def _synthetic_indoor_temperature_f(simulated_at: datetime) -> int:
    """Generate a modest seasonal temperature variation for synthetic dates."""
    monthly_baseline = {
        1: 68, 2: 68, 3: 69, 4: 70, 5: 71, 6: 72,
        7: 73, 8: 73, 9: 72, 10: 71, 11: 70, 12: 69,
    }
    return monthly_baseline[simulated_at.month]


def _record(state: dict[str, Any], description: str) -> None:
    """Insert a brief most-recent-first event and retain a compact timeline."""
    state["events"] = [f"Now · {description}", *state["events"]][:6]
