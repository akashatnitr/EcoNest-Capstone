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
    """Return aggregate power consumption for a room."""

    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{room_id}')"
            ".in('LOCATED_IN')"
            ".hasLabel('Device')"
            ".values('power_usage')"  # power_usage should become time-series metric later
            ".sum()"
        ),
    )

    results = result.get("result", [])

    return float(results[0]) if results else 0.0


async def get_user_accessible_devices(user_id: str) -> list[dict[str, Any]]:
    """Return all devices accessible to a user."""

    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{user_id}')"
            ".union("
            "out('HAS_ACCESS').hasLabel('Device'),"
            "out('HAS_ACCESS').hasLabel('Room').in('LOCATED_IN'),"
            "out('OWNS').out('CONTAINS').hasLabel('Device')"
            ").dedup().valueMap()"
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

async def get_affected_rooms(device_id: str) -> list[dict[str, Any]]:
    """Return rooms impacted if a device or circuit fails."""

    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{device_id}')"
            ".union("
            "out('LOCATED_IN'),"
            "out('DEPENDS_ON').out('LOCATED_IN'),"
            "out('POWERED_BY').in('POWERED_BY').out('LOCATED_IN')"
            ").dedup().valueMap()"
        ),
    )

    return result.get("result", [])

async def get_room_sensor_confidence(
    room_id: str,
) -> list[dict[str, Any]]:
    """Return sensor confidence coverage for a room."""

    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{room_id}')"
            ".inE('MONITORS')"
            ".as('edge')"
            ".outV()"
            ".hasLabel('Sensor')"
            ".project('sensor', 'confidence')"
            ".by(valueMap())"
            ".by(select('edge').values('confidence_score'))"
        ),
    )

    return result.get("result", [])