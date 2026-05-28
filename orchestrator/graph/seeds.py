"""Idempotent seed data helpers for the EcoNest ArcadeDB graph."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import (
    Circuit,
    Device,
    DeviceType,
    Home,
    HomeAssistantDomain,
    Room,
    RoomType,
    Sensor,
    SensorType,
    device_type_for_ha_domain,
)

DEFAULT_HOME = Home(name="Professor's House")

DEFAULT_ROOMS: tuple[dict[str, Any], ...] = (
    {"mysql_id": 1, "name": "Kitchen"},
    {"mysql_id": 2, "name": "Hallway"},
    {"mysql_id": 3, "name": "Livingroom"},
    {"mysql_id": 4, "name": "Master Bedroom"},
    {"mysql_id": 5, "name": "Bedroom 1"},
    {"mysql_id": 6, "name": "Laundry Room"},
    {"mysql_id": 7, "name": "Front Door"},
    {"mysql_id": 8, "name": "Bedroom 2"},
    {"mysql_id": 9, "name": "Bedroom 4"},
    {"mysql_id": 10, "name": "Other"},
    {"mysql_id": 11, "name": "Garage"},
    {"mysql_id": 12, "name": "Study Room"},
    {"mysql_id": 13, "name": "Media Room"},
    {"mysql_id": 14, "name": "HVAC"},
)

DEFAULT_DEVICES: tuple[dict[str, Any], ...] = (
    {"mysql_id": 1, "name": "Dishwasher", "device_type": "energy", "room_id": 1},
    {"mysql_id": 2, "name": "Kitchen GFCI 2", "device_type": "energy", "room_id": 1},
    {"mysql_id": 3, "name": "Xbox", "device_type": "energy", "room_id": 2},
    {"mysql_id": 4, "name": "Kitchen GFCI 1", "device_type": "energy", "room_id": 1},
    {"mysql_id": 5, "name": "TV Living Room", "device_type": "energy", "room_id": 3},
    {
        "mysql_id": 6,
        "name": "TV Master Bedroom",
        "device_type": "energy",
        "room_id": 4,
    },
    {
        "mysql_id": 7,
        "name": "Bedroom 1 Computer",
        "device_type": "energy",
        "room_id": 5,
    },
    {"mysql_id": 8, "name": "Washer", "device_type": "energy", "room_id": 6},
    {"mysql_id": 9, "name": "Motion Sensor", "device_type": "motion", "room_id": 7},
    {"mysql_id": 10, "name": "Sound Sensor", "device_type": "sound", "room_id": 1},
    {"mysql_id": 11, "name": "Input", "device_type": "energy", "room_id": 10},
    {"mysql_id": 12, "name": "Oven Breaker", "device_type": "energy", "room_id": 1},
    {"mysql_id": 13, "name": "AC 1", "device_type": "energy", "room_id": 14},
    {"mysql_id": 14, "name": "Fridge Breaker", "device_type": "energy", "room_id": 1},
    {
        "mysql_id": 15,
        "name": "Kitchen GFCI Breaker",
        "device_type": "energy",
        "room_id": 1,
    },
    {
        "mysql_id": 16,
        "name": "Kitchen Lights Breaker",
        "device_type": "energy",
        "room_id": 1,
    },
    {
        "mysql_id": 17,
        "name": "Microwave Breaker",
        "device_type": "energy",
        "room_id": 1,
    },
    {
        "mysql_id": 18,
        "name": "Master Bedroom Breaker",
        "device_type": "energy",
        "room_id": 4,
    },
    {
        "mysql_id": 19,
        "name": "Media Room Light Breaker",
        "device_type": "energy",
        "room_id": 13,
    },
    {"mysql_id": 20, "name": "Dryer Breaker", "device_type": "energy", "room_id": 6},
    {"mysql_id": 21, "name": "AC 2", "device_type": "energy", "room_id": 14},
    {"mysql_id": 22, "name": "Garage Breaker", "device_type": "energy", "room_id": 11},
    {
        "mysql_id": 23,
        "name": "Bedroom 2 Breaker",
        "device_type": "energy",
        "room_id": 8,
    },
    {
        "mysql_id": 24,
        "name": "Guest Room Breaker",
        "device_type": "energy",
        "room_id": 8,
    },
    {
        "mysql_id": 25,
        "name": "Bedroom 4 Breaker",
        "device_type": "energy",
        "room_id": 9,
    },
    {
        "mysql_id": 26,
        "name": "Utility Closet Breaker",
        "device_type": "energy",
        "room_id": 6,
    },
    {
        "mysql_id": 27,
        "name": "Motion Sensor Garage",
        "device_type": "motion",
        "room_id": 11,
    },
    {
        "mysql_id": 28,
        "name": "Study Room Light Breaker",
        "device_type": "energy",
        "room_id": 12,
    },
    {"mysql_id": 29, "name": "Vacuum Cleaner", "device_type": "energy", "room_id": 12},
    {
        "mysql_id": 30,
        "name": "HydraWise Monitor",
        "device_type": "energy",
        "room_id": 11,
    },
    {"mysql_id": 31, "name": "Bedroom 1 TV", "device_type": "energy", "room_id": 5},
    {"mysql_id": 32, "name": "Coffee Maker", "device_type": "energy", "room_id": 1},
    {"mysql_id": 33, "name": "Balance", "device_type": "energy", "room_id": 10},
)


class SeedRoom(BaseModel):
    """Room seed record."""

    mysql_id: int | None = Field(default=None, ge=1)
    name: str
    room_type: RoomType
    description: str | None = None
    ha_area_id: str | None = None
    floor_id: str | None = None


class SeedDevice(BaseModel):
    """Device seed record."""

    mysql_id: int | None = Field(default=None, ge=1)
    name: str
    device_type: DeviceType
    room_mysql_id: int | None = Field(default=None, ge=1)
    ha_domain: HomeAssistantDomain | None = None
    ha_entity_id: str | None = None
    ha_device_id: str | None = None
    ha_area_id: str | None = None
    ha_platform: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    via_device_id: str | None = None
    ip_address: str | None = None
    is_active: bool = True


class SeedCircuit(BaseModel):
    """Circuit seed record derived from breaker and aggregate energy devices."""

    name: str
    breaker_id: str
    max_amperage: float | None = Field(default=None, gt=0)
    device_mysql_ids: tuple[int, ...] = Field(default_factory=tuple)
    room_mysql_id: int | None = Field(default=None, ge=1)


class SeedSensor(BaseModel):
    """Sensor seed record linked to the room it monitors."""

    name: str
    sensor_type: SensorType
    room_mysql_id: int | None = Field(default=None, ge=1)
    unit: str | None = None
    ha_entity_id: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=1)


class GraphSeedInventory(BaseModel):
    """Complete graph seed payload."""

    home: Home = Field(default_factory=lambda: DEFAULT_HOME)
    rooms: list[SeedRoom] = Field(default_factory=list)
    devices: list[SeedDevice] = Field(default_factory=list)
    circuits: list[SeedCircuit] = Field(default_factory=list)
    sensors: list[SeedSensor] = Field(default_factory=list)


class SeedResult(BaseModel):
    """Counts of seeded graph objects."""

    home: str
    rooms: int
    devices: int
    circuits: int
    sensors: int
    edges: int


async def seed_graph(
    inventory: GraphSeedInventory | None = None,
) -> SeedResult:
    """Seed the full graph inventory and repair graph edges idempotently."""
    inventory = inventory or default_seed_inventory()

    home_selector = await seed_home(inventory.home)
    room_selectors = await seed_rooms("", inventory.rooms)
    device_selectors = await seed_devices(inventory.devices)
    circuit_selectors = await seed_circuits(inventory.circuits)
    sensor_selectors = await seed_sensors(inventory.sensors)

    edge_count = 0
    edge_count += await _link_home_rooms(home_selector, room_selectors)
    edge_count += await _link_devices_to_rooms(inventory.devices)
    edge_count += await _link_circuits_to_devices(inventory.circuits)
    edge_count += await _link_sensors_to_rooms(inventory.sensors)

    return SeedResult(
        home=home_selector,
        rooms=len(room_selectors),
        devices=len(device_selectors),
        circuits=len(circuit_selectors),
        sensors=len(sensor_selectors),
        edges=edge_count,
    )


def default_seed_inventory() -> GraphSeedInventory:
    """Return the EcoNest seed inventory from the current MySQL device list."""
    rooms = [_seed_room_from_row(row) for row in DEFAULT_ROOMS]
    devices = [_seed_device_from_row(row) for row in DEFAULT_DEVICES]
    return GraphSeedInventory(
        rooms=rooms,
        devices=devices,
        circuits=_infer_circuits(devices),
        sensors=_infer_sensors(devices),
    )


def build_inventory_from_records(
    rooms: Sequence[Mapping[str, Any]],
    devices: Sequence[Mapping[str, Any]],
    ha_entities: Sequence[Mapping[str, Any]] | None = None,
    ha_devices: Sequence[Mapping[str, Any]] | None = None,
    ha_areas: Sequence[Mapping[str, Any]] | None = None,
    home: Home | None = None,
) -> GraphSeedInventory:
    """Build seed inventory from MySQL rows and optional Home Assistant registries.

    HA room mapping follows entity_id -> entity.device_id -> device.area_id -> area.name.
    """
    ha_context = _HomeAssistantContext(
        entities=ha_entities or [],
        devices=ha_devices or [],
        areas=ha_areas or [],
    )
    room_seeds = [_seed_room_from_row(row, ha_context) for row in rooms]
    device_seeds = [
        _seed_device_from_row(row, ha_context)
        for row in devices
        if row.get("is_active", True)
    ]
    return GraphSeedInventory(
        home=home or DEFAULT_HOME,
        rooms=room_seeds,
        devices=device_seeds,
        circuits=_infer_circuits(device_seeds),
        sensors=_infer_sensors(device_seeds),
    )


async def seed_home(home: Home | None = None) -> str:
    """Create or update the Home node for Professor's House."""
    home = home or DEFAULT_HOME
    await _upsert_vertex(
        "Home",
        "name",
        home.name,
        {
            "name": home.name,
            "address": home.address,
            "home_assistant_url": home.home_assistant_url,
            "created_at": "datetime()",
        },
    )
    return _selector("Home", "name", home.name)


async def seed_rooms(
    home_selector: str,
    rooms: Sequence[SeedRoom] | None = None,
) -> dict[int | str, str]:
    """Create Room nodes and optionally link them to the Home node."""
    room_seeds = list(rooms or default_seed_inventory().rooms)
    selectors: dict[int | str, str] = {}
    for seed in room_seeds:
        room = Room(
            name=seed.name,
            room_type=seed.room_type,
            description=seed.description,
            ha_area_id=seed.ha_area_id,
            floor_id=seed.floor_id,
        )
        fields = {
            "mysql_id": seed.mysql_id,
            "name": room.name,
            "room_type": room.room_type,
            "description": room.description,
            "ha_area_id": room.ha_area_id,
            "floor_id": room.floor_id,
            "created_at": "datetime()",
        }
        key_name, key_value = _seed_key(seed.mysql_id, room.name)
        await _upsert_vertex("Room", key_name, key_value, fields)
        selectors[seed.mysql_id or seed.name] = _selector("Room", key_name, key_value)

    if home_selector:
        await _link_home_rooms(home_selector, selectors)
    return selectors


async def seed_devices(
    room_selectors: Mapping[int | str, str] | Sequence[SeedDevice] | None = None,
) -> dict[int | str, str]:
    """Create Device nodes from the EcoNest inventory.

    Passing a room selector mapping preserves the historical function signature.
    New callers should pass a SeedDevice sequence or use seed_graph().
    """
    if room_selectors is None or isinstance(room_selectors, Mapping):
        device_seeds = default_seed_inventory().devices
    else:
        device_seeds = list(room_selectors)

    selectors: dict[int | str, str] = {}
    for seed in device_seeds:
        device = Device(
            name=seed.name,
            device_type=seed.device_type,
            ha_domain=seed.ha_domain,
            ha_entity_id=seed.ha_entity_id,
            ha_device_id=seed.ha_device_id,
            ha_area_id=seed.ha_area_id,
            ha_platform=seed.ha_platform,
            manufacturer=seed.manufacturer,
            model=seed.model,
            ip_address=seed.ip_address,
            via_device_id=seed.via_device_id,
            is_active=seed.is_active,
        )
        fields = {
            "mysql_id": seed.mysql_id,
            "name": device.name,
            "device_type": device.device_type,
            "ha_domain": device.ha_domain,
            "ha_entity_id": device.ha_entity_id,
            "ha_device_id": device.ha_device_id,
            "ha_area_id": device.ha_area_id,
            "ha_platform": device.ha_platform,
            "manufacturer": device.manufacturer,
            "model": device.model,
            "ip_address": device.ip_address,
            "via_device_id": device.via_device_id,
            "is_active": device.is_active,
            "created_at": "datetime()",
        }
        key_name, key_value = _seed_key(seed.mysql_id, device.name)
        await _upsert_vertex("Device", key_name, key_value, fields)
        selectors[seed.mysql_id or seed.name] = _selector("Device", key_name, key_value)
    return selectors


async def seed_circuits(
    circuits: Sequence[SeedCircuit] | None = None,
) -> dict[str, str]:
    """Create Circuit nodes from breaker mappings."""
    circuit_seeds = list(circuits or default_seed_inventory().circuits)
    selectors: dict[str, str] = {}
    for seed in circuit_seeds:
        circuit = Circuit(
            name=seed.name,
            breaker_id=seed.breaker_id,
            max_amperage=seed.max_amperage,
        )
        await _upsert_vertex(
            "Circuit",
            "breaker_id",
            circuit.breaker_id or seed.breaker_id,
            {
                "name": circuit.name,
                "breaker_id": circuit.breaker_id,
                "max_amperage": circuit.max_amperage,
                "created_at": "datetime()",
            },
        )
        selectors[seed.breaker_id] = _selector("Circuit", "breaker_id", seed.breaker_id)
    return selectors


async def seed_sensors(
    room_selectors: Mapping[int | str, str] | Sequence[SeedSensor] | None = None,
) -> dict[str, str]:
    """Create Sensor nodes and link them to monitored rooms."""
    if room_selectors is None or isinstance(room_selectors, Mapping):
        sensor_seeds = default_seed_inventory().sensors
    else:
        sensor_seeds = list(room_selectors)

    selectors: dict[str, str] = {}
    for seed in sensor_seeds:
        sensor = Sensor(
            name=seed.name,
            sensor_type=seed.sensor_type,
            unit=seed.unit,
            ha_entity_id=seed.ha_entity_id,
            device_class=seed.device_class,
            state_class=seed.state_class,
        )
        await _upsert_vertex(
            "Sensor",
            "name",
            sensor.name,
            {
                "name": sensor.name,
                "sensor_type": sensor.sensor_type,
                "unit": sensor.unit,
                "ha_entity_id": sensor.ha_entity_id,
                "device_class": sensor.device_class,
                "state_class": sensor.state_class,
                "created_at": "datetime()",
            },
        )
        selectors[seed.name] = _selector("Sensor", "name", seed.name)
    return selectors


def _seed_room_from_row(
    row: Mapping[str, Any],
    ha_context: "_HomeAssistantContext | None" = None,
) -> SeedRoom:
    room_name = str(row["name"])
    ha_area = ha_context.area_by_name(room_name) if ha_context else None
    return SeedRoom(
        mysql_id=_optional_int(row.get("id") or row.get("mysql_id")),
        name=room_name,
        room_type=_infer_room_type(room_name),
        description=_optional_str(row.get("description")),
        ha_area_id=_optional_str(
            row.get("ha_area_id") or (ha_area or {}).get("area_id")
        ),
        floor_id=_optional_str(row.get("floor_id") or (ha_area or {}).get("floor_id")),
    )


def _seed_device_from_row(
    row: Mapping[str, Any],
    ha_context: "_HomeAssistantContext | None" = None,
) -> SeedDevice:
    entity = ha_context.entity_for_device_row(row) if ha_context else None
    ha_device = ha_context.device_for_entity(entity) if ha_context and entity else None
    ha_area_id = _ha_area_id(row, entity, ha_device)
    ha_domain = _domain_from_entity(entity, row.get("ha_entity_id"))
    device_type = _device_type_from_row(row, ha_domain)
    return SeedDevice(
        mysql_id=_optional_int(row.get("id") or row.get("mysql_id")),
        name=str(row["name"]),
        device_type=device_type,
        room_mysql_id=_optional_int(row.get("room_id") or row.get("room_mysql_id")),
        ha_domain=ha_domain,
        ha_entity_id=_optional_str(
            row.get("ha_entity_id") or (entity or {}).get("entity_id")
        ),
        ha_device_id=_optional_str(
            row.get("ha_device_id") or (entity or {}).get("device_id")
        ),
        ha_area_id=_optional_str(row.get("ha_area_id") or ha_area_id),
        ha_platform=_optional_str(
            row.get("ha_platform") or (entity or {}).get("platform")
        ),
        manufacturer=_optional_str(
            row.get("manufacturer") or (ha_device or {}).get("manufacturer")
        ),
        model=_optional_str(row.get("model") or (ha_device or {}).get("model")),
        via_device_id=_optional_str(
            row.get("via_device_id") or (ha_device or {}).get("via_device_id")
        ),
        ip_address=_optional_str(row.get("ip_address")),
        is_active=bool(row.get("is_active", True)),
    )


def _infer_circuits(devices: Sequence[SeedDevice]) -> list[SeedCircuit]:
    circuits: list[SeedCircuit] = []
    for device in devices:
        if not _is_circuit_like(device.name):
            continue
        breaker_id = _slug(device.name)
        device_ids = (device.mysql_id,) if device.mysql_id is not None else ()
        circuits.append(
            SeedCircuit(
                name=device.name,
                breaker_id=breaker_id,
                device_mysql_ids=device_ids,
                room_mysql_id=device.room_mysql_id,
            )
        )
    return circuits


def _infer_sensors(devices: Sequence[SeedDevice]) -> list[SeedSensor]:
    sensors: list[SeedSensor] = []
    for device in devices:
        sensor_type = _sensor_type_for_device(device)
        if sensor_type is None:
            continue
        sensors.append(
            SeedSensor(
                name=device.name,
                sensor_type=sensor_type,
                room_mysql_id=device.room_mysql_id,
                unit=_sensor_unit(sensor_type),
                ha_entity_id=device.ha_entity_id,
                confidence_score=0.9,
            )
        )
    return sensors


async def _link_home_rooms(
    home_selector: str,
    room_selectors: Mapping[int | str, str],
) -> int:
    count = 0
    for room_selector in room_selectors.values():
        await _repair_edge("CONTAINS", home_selector, room_selector)
        count += 1
    return count


async def _link_devices_to_rooms(devices: Sequence[SeedDevice]) -> int:
    count = 0
    for device in devices:
        if device.room_mysql_id is None:
            continue
        key_name, key_value = _seed_key(device.mysql_id, device.name)
        device_selector = _selector("Device", key_name, key_value)
        room_selector = _selector("Room", "mysql_id", device.room_mysql_id)
        await _repair_edge("LOCATED_IN", device_selector, room_selector)
        count += 1
    return count


async def _link_circuits_to_devices(circuits: Sequence[SeedCircuit]) -> int:
    count = 0
    for circuit in circuits:
        circuit_selector = _selector("Circuit", "breaker_id", circuit.breaker_id)
        for device_mysql_id in circuit.device_mysql_ids:
            device_selector = _selector("Device", "mysql_id", device_mysql_id)
            await _repair_edge("POWERED_BY", device_selector, circuit_selector)
            count += 1
    return count


async def _link_sensors_to_rooms(sensors: Sequence[SeedSensor]) -> int:
    count = 0
    for sensor in sensors:
        if sensor.room_mysql_id is None:
            continue
        sensor_selector = _selector("Sensor", "name", sensor.name)
        room_selector = _selector("Room", "mysql_id", sensor.room_mysql_id)
        fields = {"confidence_score": sensor.confidence_score}
        await _repair_edge("MONITORS", sensor_selector, room_selector, fields)
        count += 1
    return count


async def _upsert_vertex(
    label: str,
    key_name: str,
    key_value: Any,
    fields: Mapping[str, Any],
) -> None:
    assignments = _assignments(fields)
    command = (
        f"UPDATE {label} SET {assignments} "
        f"UPSERT WHERE {key_name} = {_sql_value(key_value)}"
    )
    await arcadedb_query("sql", command, readonly=False)


async def _repair_edge(
    edge_label: str,
    from_selector: str,
    to_selector: str,
    fields: Mapping[str, Any] | None = None,
) -> None:
    await arcadedb_query(
        "sql",
        f"DELETE EDGE {edge_label} FROM {from_selector} TO {to_selector}",
        readonly=False,
    )
    command = f"CREATE EDGE {edge_label} FROM {from_selector} TO {to_selector}"
    if fields:
        command = f"{command} SET {_assignments(fields)}"
    await arcadedb_query("sql", command, readonly=False)


def _assignments(fields: Mapping[str, Any]) -> str:
    return ", ".join(
        f"{name} = {_sql_value(value)}"
        for name, value in fields.items()
        if _has_value(value)
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _selector(label: str, key_name: str, key_value: Any) -> str:
    return f"(SELECT FROM {label} WHERE {key_name} = {_sql_value(key_value)})"


def _seed_key(mysql_id: int | None, fallback_name: str) -> tuple[str, Any]:
    if mysql_id is not None:
        return "mysql_id", mysql_id
    return "name", fallback_name


def _sql_value(value: Any) -> str:
    if value == "datetime()":
        return "datetime()"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _infer_room_type(room_name: str) -> RoomType:
    normalized = room_name.lower()
    if "bed" in normalized:
        return RoomType.BEDROOM
    if "kitchen" in normalized:
        return RoomType.KITCHEN
    if "garage" in normalized:
        return RoomType.GARAGE
    if "bath" in normalized:
        return RoomType.BATHROOM
    if "media" in normalized or "tv" in normalized:
        return RoomType.MEDIA_ROOM
    if "laundry" in normalized:
        return RoomType.LAUNDRY
    if "study" in normalized or "office" in normalized:
        return RoomType.OFFICE
    if "front door" in normalized or "yard" in normalized or "outdoor" in normalized:
        return RoomType.OUTDOOR
    if "hvac" in normalized or "utility" in normalized:
        return RoomType.UTILITY
    if "other" in normalized:
        return RoomType.OTHER
    return RoomType.LIVING_ROOM


def _device_type_from_row(
    row: Mapping[str, Any],
    ha_domain: HomeAssistantDomain | None,
) -> DeviceType:
    device_type = str(row.get("device_type", "")).lower()
    type_map = {
        "energy": DeviceType.ENERGY_MONITOR,
        "motion": DeviceType.MOTION_SENSOR,
        "sound": DeviceType.SOUND_SENSOR,
        "smart_plug": DeviceType.SMART_PLUG,
        "motion_sensor": DeviceType.MOTION_SENSOR,
        "sound_sensor": DeviceType.SOUND_SENSOR,
        "light": DeviceType.SMART_BULB,
        "switch": DeviceType.SMART_SWITCH,
        "cover": DeviceType.COVER,
        "climate": DeviceType.THERMOSTAT,
        "valve": DeviceType.VALVE,
        "fan": DeviceType.FAN,
        "media_player": DeviceType.MEDIA_PLAYER,
        "sensor": DeviceType.SENSOR,
    }
    if device_type in type_map:
        return type_map[device_type]
    if ha_domain is not None:
        return device_type_for_ha_domain(ha_domain)
    return DeviceType.OTHER


def _domain_from_entity(
    entity: Mapping[str, Any] | None,
    entity_id: Any,
) -> HomeAssistantDomain | None:
    raw_entity_id = _optional_str(entity_id or (entity or {}).get("entity_id"))
    if not raw_entity_id or "." not in raw_entity_id:
        return None
    domain = raw_entity_id.split(".", maxsplit=1)[0]
    try:
        return HomeAssistantDomain(domain)
    except ValueError:
        return None


def _ha_area_id(
    row: Mapping[str, Any],
    entity: Mapping[str, Any] | None,
    ha_device: Mapping[str, Any] | None,
) -> str | None:
    return _optional_str(
        row.get("ha_area_id")
        or (entity or {}).get("area_id")
        or (ha_device or {}).get("area_id")
    )


def _sensor_type_for_device(device: SeedDevice) -> SensorType | None:
    if device.device_type == DeviceType.MOTION_SENSOR:
        return SensorType.MOTION
    if device.device_type == DeviceType.SOUND_SENSOR:
        return SensorType.SOUND
    return None


def _sensor_unit(sensor_type: SensorType) -> str:
    if sensor_type == SensorType.MOTION:
        return "binary"
    if sensor_type == SensorType.SOUND:
        return "dB"
    return "value"


def _is_circuit_like(name: str) -> bool:
    normalized = name.lower()
    return (
        "breaker" in normalized
        or normalized.startswith("ac ")
        or normalized in {"balance", "input"}
    )


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result if result else None


class _HomeAssistantContext:
    def __init__(
        self,
        entities: Sequence[Mapping[str, Any]],
        devices: Sequence[Mapping[str, Any]],
        areas: Sequence[Mapping[str, Any]],
    ) -> None:
        self.entities = list(entities)
        self.devices_by_id = {
            str(device["id"]): device for device in devices if device.get("id")
        }
        self.areas_by_id = {
            str(area["area_id"]): area for area in areas if area.get("area_id")
        }
        self.areas_by_name = {
            _normalize_name(str(area["name"])): area
            for area in areas
            if area.get("name")
        }

    def area_by_name(self, name: str) -> Mapping[str, Any] | None:
        return self.areas_by_name.get(_normalize_name(name))

    def entity_for_device_row(
        self,
        row: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        entity_id = row.get("ha_entity_id")
        if entity_id:
            for entity in self.entities:
                if entity.get("entity_id") == entity_id:
                    return entity

        target_name = _normalize_name(str(row["name"]))
        for entity in self.entities:
            if entity.get("disabled_by"):
                continue
            candidate_names = [
                entity.get("entity_id", "").split(".", maxsplit=1)[-1],
                entity.get("name"),
                entity.get("original_name"),
            ]
            if any(
                _normalize_name(str(name)) == target_name
                for name in candidate_names
                if name
            ):
                return entity
        return None

    def device_for_entity(
        self,
        entity: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        device_id = entity.get("device_id")
        if not device_id:
            return None
        return self.devices_by_id.get(str(device_id))


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
