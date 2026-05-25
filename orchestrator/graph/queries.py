"""Gremlin query helpers for the ArcadeDB graph."""

from typing import Any

from orchestrator.core.database import arcadedb_query


async def get_devices_in_room(room_id: str) -> list[dict[str, Any]]:
    """Return all devices located in a given room."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(room_id)}')"
            ".union("
            "in('LOCATED_IN').hasLabel('Device'),"
            "out('CONTAINS').hasLabel('Device')"
            ")"
            ".dedup()"
            ".valueMap(true)"
        ),
    )
    return _result_list(result)


async def get_room_power_consumption(room_id: str) -> float:
    """Return aggregate synced power readings for a room."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(room_id)}')"
            ".as('room')"
            ".V()"
            ".hasLabel('SensorReading')"
            ".where(eq('room')).by('room_id').by('mysql_id')"
            ".values('data')"
            ".select('power')"
            ".sum()"
        ),
    )
    return _first_float(result)


async def get_user_accessible_devices(user_id: str) -> list[dict[str, Any]]:
    """Return all devices accessible to a user."""
    user_vertex = f"g.V('{_escape_gremlin_string(user_id)}')"
    result = await arcadedb_query(
        "gremlin",
        (
            f"{user_vertex}"
            ".union("
            "out('HAS_ACCESS').hasLabel('Device'),"
            "out('HAS_ACCESS').hasLabel('Room').in('LOCATED_IN'),"
            "out('HAS_ACCESS').hasLabel('Room').out('CONTAINS').hasLabel('Device'),"
            "out('OWNS').out('CONTAINS').hasLabel('Room').in('LOCATED_IN'),"
            "out('OWNS').out('CONTAINS').hasLabel('Room').out('CONTAINS').hasLabel('Device')"
            ")"
            ".dedup()"
            ".valueMap(true)"
        ),
    )
    return _result_list(result)


async def get_circuit_devices(circuit_id: str) -> list[dict[str, Any]]:
    """Return devices powered by a circuit."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(circuit_id)}')"
            ".in('POWERED_BY')"
            ".hasLabel('Device')"
            ".valueMap(true)"
        ),
    )
    return _result_list(result)


async def get_sensor_coverage(room_id: str) -> list[dict[str, Any]]:
    """Return sensors that monitor a given room."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(room_id)}')"
            ".in('MONITORS')"
            ".hasLabel('Sensor')"
            ".valueMap(true)"
        ),
    )
    return _result_list(result)


async def get_affected_rooms(device_id: str) -> list[dict[str, Any]]:
    """Return rooms impacted if a device or circuit fails."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(device_id)}')"
            ".union("
            "out('LOCATED_IN'),"
            "in('DEPENDS_ON').out('LOCATED_IN'),"
            "out('POWERED_BY').in('POWERED_BY').out('LOCATED_IN')"
            ")"
            ".dedup()"
            ".valueMap(true)"
        ),
    )
    return _result_list(result)


async def get_room_sensor_confidence(
    room_id: str,
) -> list[dict[str, Any]]:
    """Return sensor confidence coverage for a room."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{_escape_gremlin_string(room_id)}')"
            ".inE('MONITORS')"
            ".as('edge')"
            ".outV()"
            ".hasLabel('Sensor')"
            ".project('sensor', 'confidence')"
            ".by(valueMap(true))"
            ".by(select('edge').values('confidence_score'))"
        ),
    )
    return _result_list(result)


def _result_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("result", [])
    return [row for row in rows if isinstance(row, dict)]


def _first_float(result: dict[str, Any]) -> float:
    rows = result.get("result", [])
    if not rows:
        return 0.0
    value = rows[0]
    if isinstance(value, list):
        value = value[0] if value else 0.0
    return float(value or 0.0)


def _escape_gremlin_string(value: str) -> str:
    """Escape a string for single-quoted Gremlin literals."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
