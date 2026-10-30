"""Scenario definitions for EcoNest's safe, visual simulated households."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from orchestrator.core.home_io_catalog import STANDARD_HOME_IO_POINTS

CITY_APARTMENT_NAME = "City Apartment Simulation"
CASAS_ARUBA_NAME = "CASAS Aruba Replay"
CN_OBEE_NAME = "CN-OBEE Beijing Apartment"
HOME_IO_NAME = "Home I/O Interactive Test Home"

_CITY_APARTMENT_STATE: dict[str, Any] = {
    "scenario": "city_apartment", "name": "City Apartment",
    "subtitle": "Weekday evening · one resident", "updated_label": "Simulated live state",
    "summary": {"occupancy": "Home", "indoor_temperature_f": 74, "power_w": 412, "active_rooms": 2},
    "rooms": [
        {"id":"bedroom","name":"Bedroom","x":5,"y":7,"width":38,"height":40,"temperature_f":72,"occupied":False},
        {"id":"bathroom","name":"Bathroom","x":5,"y":54,"width":38,"height":37,"temperature_f":73,"occupied":False},
        {"id":"living_room","name":"Living room","x":50,"y":7,"width":45,"height":48,"temperature_f":74,"occupied":True},
        {"id":"kitchen","name":"Kitchen","x":50,"y":62,"width":45,"height":29,"temperature_f":75,"occupied":True},
    ],
    "devices": [
        {"id":"bedroom_thermostat","room":"bedroom","kind":"thermostat","label":"Thermostat","detail":"72°F · set to 72°F","x":35,"y":17,"state":"normal"},
        {"id":"bedroom_dimmer","room":"bedroom","kind":"light","label":"Dimmer light","detail":"Off · activity proxy","x":18,"y":31,"state":"off"},
        {"id":"bathroom_motion","room":"bathroom","kind":"motion","label":"Motion sensor","detail":"No motion","x":30,"y":73,"state":"idle"},
        {"id":"living_thermostat","room":"living_room","kind":"thermostat","label":"Thermostat","detail":"74°F · set to 73°F","x":82,"y":17,"state":"normal"},
        {"id":"living_dimmer","room":"living_room","kind":"light","label":"Dimmer light","detail":"On · motion inferred","x":62,"y":31,"state":"on"},
        {"id":"tv_plug","room":"living_room","kind":"power","label":"TV smart plug","detail":"140 W","x":82,"y":41,"state":"active"},
        {"id":"kitchen_dimmer","room":"kitchen","kind":"light","label":"Dimmer light","detail":"On · motion inferred","x":62,"y":76,"state":"on"},
        {"id":"kitchen_energy","room":"kitchen","kind":"power","label":"Energy meter","detail":"272 W","x":84,"y":76,"state":"active"},
    ],
    "events": ["6:18 PM · Motion inferred in Kitchen from dimmer light.", "6:12 PM · Living room dimmer light turned on.", "5:47 PM · Resident arrived home."],
}


def city_apartment_state() -> dict[str, Any]:
    """Return an isolated copy of the initial City Apartment scenario state."""
    return deepcopy(_CITY_APARTMENT_STATE)


def home_io_state() -> dict[str, Any]:
    """Return the standard Home I/O house using its verified A--M catalog."""
    zones = tuple("ABCDEFGHIJKLM")
    devices: list[dict[str, Any]] = []
    for point in STANDARD_HOME_IO_POINTS:
        initial_state = "off" if point.writable else "idle"
        if point.kind == "smoke":
            initial_state = "normal"
        if point.identifier.endswith("room_temperature"):
            initial_state = "normal"
        if point.identifier.endswith("thermostat_set_point"):
            initial_state = "set"
        detail = "Off · Home I/O output" if point.writable else "Idle · Home I/O sensor"
        if point.identifier.endswith("room_temperature"):
            detail = "21.7°C / 71°F · Home I/O room-temperature memory"
        elif point.identifier.endswith("thermostat_set_point"):
            detail = "21.0°C / 69.8°F · Live Home I/O thermostat setting"
        devices.append({
            "id": point.identifier,
            "room": point.zone.lower(),
            "kind": point.kind,
            "label": point.label,
            "detail": detail,
            "state": initial_state,
            "live_writable": point.writable,
            "momentary": point.momentary,
            "data_type": point.data_type,
            "temperature_control": point.identifier.endswith("thermostat_set_point"),
            "temperature_sensor": point.identifier.endswith("room_temperature"),
            "address": point.address,
        })
    return {
        "scenario": "home_io",
        "name": HOME_IO_NAME,
        "subtitle": "Live Home I/O teaching house · verified A–M device catalog",
        "updated_label": "Home I/O standard-house inventory",
        "source": {"name": "Home I/O 3D smart-home simulator", "url": "https://teachathomeio.com/", "license": "Commercial software · local licensed simulator", "kind": "simulator"},
        "summary": {"occupancy": "Home", "indoor_temperature_f": 71, "power_w": 0, "active_rooms": 0},
        "rooms": [
            {"id": zone.lower(), "name": zone, "x": 0, "y": 0, "width": 0, "height": 0, "temperature_f": 71, "occupied": False}
            for zone in zones
        ],
        "devices": devices,
        "events": ["Home I/O standard A–M catalog is ready; only blue External-Mode devices accept live commands."],
        "dataset_overview": {"coverage": "Built-in Home I/O teaching house · zones A–M", "source_range": "Live licensed desktop simulator", "signals": "Verified bit outputs and presence/safety sensor inputs", "replay_note": "EcoNest controls only allow-listed simulator outputs. Nothing here controls a physical home."},
        "live_simulation": {"clock": "EcoNest local time", "activity": "Live Home I/O state", "mode": "EcoNest demo mode · Home I/O bridge disconnected"},
    }


# A compact, replayable extract of CASAS Aruba's labelled event stream.  The
# full CC-BY source is intentionally not committed; this keeps the application
# lightweight while retaining real timestamps, sensor IDs, values and labels.
# Source: Cook et al., CASAS Aruba, Zenodo 17180309.
_CASAS_ARUBA_STATE: dict[str, Any] = {
    "scenario": "casas_aruba",
    "name": "CASAS Aruba Replay",
    "subtitle": "Synthetic 2026 household · calibrated from CASAS Aruba",
    "updated_label": "Present-day simulated state · CASAS-calibrated",
    "source": {
        "name": "CASAS Smart Home Dataset (Aruba)",
        "url": "https://zenodo.org/records/17180309",
        "license": "CC BY 4.0",
    },
    "summary": {"occupancy": "Home", "indoor_temperature_f": 67, "power_w": 0, "active_rooms": 2},
    "rooms": [
        {"id": "bedroom", "name": "Bedroom", "x": 5, "y": 7, "width": 39, "height": 39, "temperature_f": 67, "occupied": True},
        {"id": "bathroom", "name": "Bathroom", "x": 5, "y": 54, "width": 39, "height": 37, "temperature_f": 67, "occupied": True},
        {"id": "living_room", "name": "Living room", "x": 50, "y": 7, "width": 45, "height": 48, "temperature_f": 67, "occupied": False},
        {"id": "kitchen", "name": "Kitchen", "x": 50, "y": 62, "width": 45, "height": 29, "temperature_f": 67, "occupied": False},
    ],
    "devices": [
        {"id": "m003", "room": "bedroom", "kind": "motion", "label": "M003 bed motion", "detail": "ON · Sleeping ended at 5:40:43 AM", "x": 28, "y": 25, "state": "active"},
        {"id": "m005", "room": "bedroom", "kind": "motion", "label": "M005 bedroom motion", "detail": "ON · transition to bathroom", "x": 18, "y": 34, "state": "active"},
        {"id": "m004", "room": "bathroom", "kind": "motion", "label": "M004 bathroom motion", "detail": "ON · Bed_to_Toilet activity", "x": 29, "y": 72, "state": "active"},
        {"id": "m007", "room": "bedroom", "kind": "motion", "label": "M007 room motion", "detail": "ON · activity transition", "x": 37, "y": 14, "state": "active"},
        {"id": "t002", "room": "bedroom", "kind": "thermostat", "label": "T002 temperature sensor", "detail": "20°C / 68°F · recorded sensor value", "x": 12, "y": 17, "state": "normal"},
    ],
    "events": [
        "5:43 AM · M004 ended the labelled Bed_to_Toilet activity.",
        "5:40 AM · M004 began the labelled Bed_to_Toilet activity.",
        "5:40 AM · M003 ended the labelled Sleeping activity.",
        "5:33 AM · T003 recorded 19°C / 66°F.",
    ],
}

# The published Aruba room-to-sensor mapping.  Drawing coordinates are a
# readable schematic for EcoNest, not a claim that the original floor plan is
# geometrically to scale.  Sensor IDs and room membership are retained so the
# agent can reason over the complete ambient inventory.
_CASAS_ARUBA_LAYOUT: tuple[tuple[str, str, int, int, int, int], ...] = (
    ("bedroom_1", "Bedroom 1", 4, 5, 27, 25),
    ("bedroom_2", "Bedroom 2", 4, 35, 27, 22),
    ("bathroom_1", "Bathroom 1", 4, 62, 27, 29),
    ("bathroom_2", "Bathroom 2", 35, 62, 18, 29),
    ("living_room", "Living room", 35, 5, 29, 25),
    ("dining", "Dining", 68, 5, 27, 18),
    ("kitchen", "Kitchen", 68, 27, 27, 30),
    ("office", "Office", 57, 62, 38, 29),
    ("hall_1", "Hall 1", 35, 35, 18, 22),
    ("hall_2", "Hall 2", 57, 35, 7, 22),
    ("front_entry", "Front entry", 68, 61, 12, 10),
    ("back_entry", "Back entry", 83, 61, 12, 10),
    ("garage", "Garage entry", 83, 74, 12, 17),
)

_CASAS_ARUBA_SENSOR_ROOMS: dict[str, tuple[str, ...]] = {
    "bedroom_1": ("M001", "M002", "M003", "M005", "M006", "M007"),
    "bedroom_2": ("M023", "M024"),
    "bathroom_1": ("M029", "M031", "D003"),
    "bathroom_2": ("M004",),
    "living_room": ("M009", "M010", "M012", "M013", "M020"),
    "dining": ("M014",),
    "kitchen": ("M015", "M016", "M017", "M018", "M019"),
    "office": ("M025", "M026", "M027", "M028"),
    "hall_1": ("M008",),
    "hall_2": ("M021", "M022"),
    "front_entry": ("M011", "D001"),
    "back_entry": ("D002",),
    "garage": ("M030", "D004"),
}

_CASAS_TEMPERATURE_ROOMS: dict[str, str] = {
    "T001": "bedroom_1", "T002": "living_room", "T003": "kitchen",
    "T004": "bathroom_1", "T005": "hall_1",
}


def _casas_room_state() -> list[dict[str, Any]]:
    """Build a schematic of every documented Aruba zone."""
    active = {"bedroom_1", "bathroom_2"}
    return [
        {
            "id": room_id, "name": name, "x": x, "y": y, "width": width,
            "height": height, "temperature_f": 67, "occupied": room_id in active,
        }
        for room_id, name, x, y, width, height in _CASAS_ARUBA_LAYOUT
    ]


def _casas_device_state() -> list[dict[str, Any]]:
    """Build all 40 documented Aruba ambient sensors for the floor-plan view."""
    layout = {row[0]: row for row in _CASAS_ARUBA_LAYOUT}
    devices: list[dict[str, Any]] = []
    active = {"M003", "M004", "M005", "M007"}
    for room_id, sensor_ids in _CASAS_ARUBA_SENSOR_ROOMS.items():
        _, room_name, x, y, width, height = layout[room_id]
        for index, sensor_id in enumerate(sensor_ids):
            columns = min(3, len(sensor_ids))
            device_x = x + 5 + (index % columns) * max(5, (width - 10) // columns)
            device_y = y + 10 + (index // columns) * 9
            is_door = sensor_id.startswith("D")
            sensor_active = sensor_id in active
            devices.append({
                "id": sensor_id.lower(), "room": room_id,
                "kind": "door" if is_door else "motion",
                "label": (
                    f"{room_name} · {sensor_id} "
                    f"{'door contact' if is_door else 'motion sensor'}"
                ),
                "detail": (
                    "ON · active in the selected replay window" if sensor_active
                    else "No trigger in the selected replay window"
                ),
                "x": device_x, "y": device_y,
                "state": "active" if sensor_active else "idle",
            })
    for index, (sensor_id, room_id) in enumerate(_CASAS_TEMPERATURE_ROOMS.items()):
        _, room_name, x, y, width, height = layout[room_id]
        devices.append({
            "id": sensor_id.lower(), "room": room_id, "kind": "thermostat",
            "label": f"{room_name} · {sensor_id} temperature sensor",
            "detail": "Recorded ambient temperature · 20°C / 68°F",
            "x": x + width - 6, "y": y + height - 7 - (index % 2) * 5,
            "state": "normal",
        })
    return devices


def casas_aruba_state() -> dict[str, Any]:
    """Return an isolated snapshot of the CASAS Aruba replay scenario."""
    state = deepcopy(_CASAS_ARUBA_STATE)
    state["rooms"] = _casas_room_state()
    state["devices"] = _casas_device_state()
    state["visual_overlays"] = {
        "resident_arriving": False,
        "resident_visible": True,
        "resident_room": "bedroom_1",
        "car_present": True,
    }
    state["devices"].extend([
        {
            "id": "virtual_living_light", "room": "living_room", "kind": "light",
            "label": "Virtual living-room light", "detail": "Off · EcoNest simulation actuator",
            "x": 49, "y": 19, "state": "off", "virtual": True,
        },
        {
            "id": "virtual_kitchen_light", "room": "kitchen", "kind": "light",
            "label": "Virtual kitchen light", "detail": "Off · EcoNest simulation actuator",
            "x": 82, "y": 43, "state": "off", "virtual": True,
        },
        {
            "id": "virtual_energy_meter", "room": "living_room", "kind": "power",
            "label": "Virtual energy meter", "detail": "0 W · simulated baseline",
            "x": 59, "y": 25, "state": "idle", "virtual": True,
        },
    ])
    state["dataset_overview"] = {
        "coverage": "One community home, collected 2010–2011",
        "source_range": "Recorded source: Nov 4, 2010 – Jun 11, 2011",
        "simulation_range": "Synthetic continuation: Jun 12 – Nov 3 and all 2026 dates",
        "events": "1,719,558 timestamped records",
        "sensors": "31 PIR motion · 4 magnetic door · 5 temperature",
        "activities": (
            "Bed_to_Toilet, Eating, Enter_Home, Housekeeping, Leave_Home, "
            "Meal_Preparation, Relax, Respirate, Sleeping, Wash_Dishes, Work"
        ),
        "simulation_activities": (
            "Sleeping, Bed_to_Toilet, Personal_Hygiene (synthetic transition), "
            "Meal_Preparation, Eating, Relax, Work, Leave_Home"
        ),
        "replay_note": (
            "The source range is real CASAS data; missing calendar dates and 2026 "
            "are generated from its observed activity patterns. Virtual lights and "
            "power are an EcoNest testing overlay, not CASAS measurements."
        ),
    }
    state["live_simulation"] = {
        "clock": "2010-11-04 05:40 AM",
        "activity": "Bed_to_Toilet",
        "mode": "Dataset-calibrated live simulation",
        "step": 1,
    }
    return state


def cn_obee_state() -> dict[str, Any]:
    """Return a reproducible, source-backed CN-OBEE apartment replay.

    The compact state uses one real per-minute record from the public source.
    The full 188 MB CSV distribution is intentionally not committed; its schema
    and source timestamp are retained here for transparent simulation behavior.
    """
    rooms = (
        ("cloakroom", "Cloakroom", 4, 6, 22, 24, 21.19, 52.14, False, False),
        ("home_office", "Home office", 4, 35, 22, 27, 20.08, 55.09, False, False),
        ("kitchen", "Kitchen", 31, 6, 29, 31, 19.21, 60.28, True, False),
        ("living_room", "Living room", 65, 6, 30, 31, 18.27, 62.05, False, True),
        ("master_bedroom", "Master bedroom", 31, 42, 29, 40, 21.29, 52.80, False, True),
        ("secondary_bedroom", "Secondary bedroom", 65, 42, 30, 40, 20.15, 54.93, False, False),
    )
    state: dict[str, Any] = {
        "scenario": "cn_obee",
        "name": CN_OBEE_NAME,
        "subtitle": "Beijing apartment · source-backed per-minute replay",
        "updated_label": "Recorded CN-OBEE state",
        "source": {
            "name": "CN-OBEE: occupant behavior, thermal environment, and appliance electricity use",
            "url": "https://doi.org/10.6084/m9.figshare.23686476.v1",
            "license": "CC0",
        },
        "summary": {
            "occupancy": "Home",
            "indoor_temperature_f": 70,
            "power_w": 59,
            "active_rooms": 2,
        },
        "rooms": [],
        "devices": [],
        "visual_overlays": {
            "resident_visible": False,
            "resident_room": None,
        },
        "events": [
            "Recorded source · Living room occupancy recorded.",
            "Recorded source · Master bedroom occupancy recorded.",
            "Recorded source · Kitchen window was recorded open.",
            "Recorded source · Fridge power was 51.01 W.",
        ],
        "dataset_overview": {
            "coverage": "One single-family apartment in Beijing, China",
            "source_range": "Recorded one-year source window",
            "sampling": "Per-minute room and appliance records; hourly outdoor weather",
            "events": "525,601 per-minute source timestamps",
            "sensors": "6 room environment/occupancy profiles · window contacts · 14 appliance-power channels",
            "activities": "Room occupancy presence (0/1); no manually annotated activity labels",
            "rooms": "Cloakroom, Home office, Kitchen, Living room, Master bedroom, Secondary bedroom",
            "signals": "Occupancy · temperature · humidity · pressure · window state · appliance power · outdoor weather",
            "appliances": "Electric kettle, fridge, rice cooker, computer, TV, water heaters, washing machine, six air conditioners",
            "replay_note": "Current state is an exact public source record; the full CSV is loaded only when running a full replay import.",
        },
        "live_simulation": {
            "clock": "Live-paced local simulation time",
            "activity": "Night-time occupancy",
            "mode": "Recorded CN-OBEE replay",
            "source_timestamp": "2021-10-15T02:09:00",
        },
    }
    for room_id, name, x, y, width, height, temp_c, humidity, window_open, occupied in rooms:
        state["rooms"].append({
            "id": room_id,
            "name": name,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "temperature_f": round(temp_c * 9 / 5 + 32),
            "occupied": occupied,
        })
        state["devices"].extend([
            {
                "id": f"{room_id}_temperature",
                "room": room_id,
                "kind": "thermostat",
                "label": f"{name} temperature sensor",
                "detail": f"{temp_c:.2f}°C / {temp_c * 9 / 5 + 32:.1f}°F · recorded",
                "x": x + width - 6,
                "y": y + 8,
                "state": "normal",
            },
            {
                "id": f"{room_id}_occupancy",
                "room": room_id,
                "kind": "motion",
                "label": f"{name} occupancy sensor",
                "detail": "Occupied · recorded" if occupied else "Unoccupied · recorded",
                "x": x + width // 2,
                "y": y + height // 2,
                "state": "active" if occupied else "idle",
            },
        ])
        if room_id != "cloakroom":
            state["devices"].append({
                "id": f"{room_id}_window",
                "room": room_id,
                "kind": "door",
                "label": f"{name} window sensor",
                "detail": "Open · recorded" if window_open else "Closed · recorded",
                "x": x + 6,
                "y": y + 8,
                "state": "active" if window_open else "idle",
            })
    power_channels = (
        ("electric_kettle", "Electric kettle", "kitchen", 0.50),
        ("fridge", "Fridge", "kitchen", 51.01),
        ("rice_cooker", "Rice cooker", "kitchen", 0.00),
        ("computer", "Computer", "home_office", 0.00),
        ("tv", "TV", "living_room", 0.54),
        ("washing_machine", "Washing machine", "kitchen", 0.78),
        ("air_conditioner_1", "Air conditioner 1", "living_room", 0.00),
        ("air_conditioner_2", "Air conditioner 2", "master_bedroom", 1.35),
    )
    for index, (device_id, label, room_id, power_w) in enumerate(power_channels):
        state["devices"].append({
            "id": device_id,
            "room": room_id,
            "kind": "power",
            "label": label,
            "detail": f"{power_w:.2f} W · recorded appliance channel",
            "x": 9 + (index % 4) * 21,
            "y": 88 + (index // 4) * 6,
            "state": "active" if power_w >= 5 else "idle",
        })
    state["devices"].extend([
        {
            "id": "virtual_living_light",
            "room": "living_room",
            "kind": "light",
            "label": "Virtual living-room light",
            "detail": "Off · EcoNest simulation actuator",
            "x": 78,
            "y": 26,
            "state": "off",
            "virtual": True,
        },
        {
            "id": "virtual_kitchen_light",
            "room": "kitchen",
            "kind": "light",
            "label": "Virtual kitchen light",
            "detail": "Off · EcoNest simulation actuator",
            "x": 45,
            "y": 23,
            "state": "off",
            "virtual": True,
        },
    ])
    return state


def simulated_household_state(name: str) -> dict[str, Any] | None:
    """Return the initial state for a named supported simulated household."""
    if name == CITY_APARTMENT_NAME:
        return city_apartment_state()
    if name == CASAS_ARUBA_NAME:
        return casas_aruba_state()
    if name == CN_OBEE_NAME:
        return cn_obee_state()
    if name == HOME_IO_NAME:
        return home_io_state()
    return None
