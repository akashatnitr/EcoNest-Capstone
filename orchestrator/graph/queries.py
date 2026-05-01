"""Gremlin query helpers for the ArcadeDB graph."""

from typing import Any

from orchestrator.core.database import arcadedb_query


async def get_devices_in_room(room_id: str) -> list[dict[str, Any]]:
    """Return all devices located in a given room."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{room_id}').in('LOCATED_IN').hasLabel('Device').valueMap()",
    )
    return result.get("result", [])


async def get_room_power_consumption(room_id: str) -> float:
    """Return aggregate power consumption for a room's devices."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{room_id}').in('LOCATED_IN').hasLabel('Device')"
            ".values('current_power').sum()"
        ),
    )
    results = result.get("result", [])
    return float(results[0]) if results else 0.0


async def get_user_accessible_devices(user_id: str) -> list[dict[str, Any]]:
    """Return devices the user has access to (via HAS_ACCESS or ownership)."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{user_id}').out('HAS_ACCESS').hasLabel('Device').valueMap().union("
            f"g.V('{user_id}').out('OWNS').out('CONTAINS').hasLabel('Device').valueMap())"
        ),
    )
    return result.get("result", [])


async def get_circuit_devices(circuit_id: str) -> list[dict[str, Any]]:
    """Return devices powered by a circuit."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{circuit_id}').in('POWERED_BY').hasLabel('Device').valueMap()",
    )
    return result.get("result", [])


async def get_sensor_coverage(room_id: str) -> list[dict[str, Any]]:
    """Return sensors that monitor a given room."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{room_id}').in('MONITORS').hasLabel('Sensor').valueMap()",
    )
    return result.get("result", [])
