"""Seed data from existing database_schema.txt + HA configs."""

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import Circuit, Device, Home, Room, Sensor


async def seed_home() -> str:
    """Create the Home node for Professor's House."""
    home = Home(name="Professor's House")
    resp = await arcadedb_query(
        "sql",
        f"CREATE VERTEX Home SET name = '{home.name}', created_at = datetime()",
        readonly=False,
    )
    return _extract_rid(resp) or ""


async def seed_rooms(home_rid: str) -> dict[str, str]:
    """Create Room nodes and link them to Home."""
    rooms = [
        Room(name="Kitchen", room_type="Kitchen"),
        Room(name="Master Bedroom", room_type="Bedroom"),
        Room(name="Garage", room_type="Garage"),
        Room(name="Living Room", room_type="LivingRoom"),
        Room(name="Media Room", room_type="MediaRoom"),
    ]
    rid_map: dict[str, str] = {}
    for room in rooms:
        resp = await arcadedb_query(
            "sql",
            (
                f"CREATE VERTEX Room SET name = '{room.name}', "
                f"room_type = '{room.room_type}', created_at = datetime()"
            ),
            readonly=False,
        )
        rid = _extract_rid(resp)
        if rid:
            rid_map[room.name] = rid
            await arcadedb_query(
                "sql",
                f"CREATE EDGE CONTAINS FROM {home_rid} TO {rid}",
                readonly=False,
            )
    return rid_map


async def seed_devices(room_rid_map: dict[str, str]) -> dict[str, str]:
    """Create Device nodes from existing Emporia Vue + Kasa plug inventory."""
    devices = [
        Device(name="Xbox", device_type="SmartSwitch", ha_entity_id="switch.xbox"),
        Device(
            name="TV Living Room", device_type="SmartSwitch", ha_entity_id="switch.sp5"
        ),
        Device(
            name="TV Master Bedroom",
            device_type="SmartSwitch",
            ha_entity_id="switch.sp6",
        ),
        Device(
            name="Washer",
            device_type="SmartPlug",
            ha_entity_id="sensor.washer_machine_state",
        ),
        Device(
            name="Dryer",
            device_type="SmartPlug",
            ha_entity_id="sensor.dryer_machine_state",
        ),
    ]
    rid_map: dict[str, str] = {}
    for device in devices:
        resp = await arcadedb_query(
            "sql",
            (
                f"CREATE VERTEX Device SET name = '{device.name}', "
                f"device_type = '{device.device_type}', "
                f"ha_entity_id = '{device.ha_entity_id or ''}', "
                f"is_active = true, created_at = datetime()"
            ),
            readonly=False,
        )
        rid = _extract_rid(resp)
        if rid:
            rid_map[device.name] = rid
    return rid_map


async def seed_circuits() -> dict[str, str]:
    """Create Circuit nodes from breaker mappings."""
    circuits = [
        Circuit(name="Breaker 2 - Oven", breaker_id="breaker_2"),
        Circuit(name="Balance Power", breaker_id="balance"),
    ]
    rid_map: dict[str, str] = {}
    for circuit in circuits:
        resp = await arcadedb_query(
            "sql",
            (
                f"CREATE VERTEX Circuit SET name = '{circuit.name}', "
                f"breaker_id = '{circuit.breaker_id or ''}', "
                f"created_at = datetime()"
            ),
            readonly=False,
        )
        rid = _extract_rid(resp)
        if rid:
            rid_map[circuit.name] = rid
    return rid_map


async def seed_sensors(room_rid_map: dict[str, str]) -> dict[str, str]:
    """Create Sensor nodes and link them to rooms."""
    sensors = [
        Sensor(name="Front Door Motion", sensor_type="motion", unit="binary"),
        Sensor(name="Garage Motion", sensor_type="motion", unit="binary"),
        Sensor(name="Soil Moisture", sensor_type="soil", unit="%"),
    ]
    rid_map: dict[str, str] = {}
    for sensor in sensors:
        resp = await arcadedb_query(
            "sql",
            (
                f"CREATE VERTEX Sensor SET name = '{sensor.name}', "
                f"sensor_type = '{sensor.sensor_type}', "
                f"unit = '{sensor.unit or ''}', created_at = datetime()"
            ),
            readonly=False,
        )
        rid = _extract_rid(resp)
        if rid:
            rid_map[sensor.name] = rid
    # Link sensors to rooms
    if "Front Door Motion" in rid_map and "Living Room" in room_rid_map:
        await arcadedb_query(
            "sql",
            f"CREATE EDGE MONITORS FROM {rid_map['Front Door Motion']} TO {room_rid_map['Living Room']}",
            readonly=False,
        )
    if "Garage Motion" in rid_map and "Garage" in room_rid_map:
        await arcadedb_query(
            "sql",
            f"CREATE EDGE MONITORS FROM {rid_map['Garage Motion']} TO {room_rid_map['Garage']}",
            readonly=False,
        )
    return rid_map


def _extract_rid(response: dict) -> str | None:
    """Extract the @rid from an ArcadeDB CREATE VERTEX response."""
    results = response.get("result", [])
    if results and isinstance(results[0], dict):
        return results[0].get("@rid")
    return None
