"""Validate graph consistency against the ontology."""

from typing import Any

from orchestrator.core.database import arcadedb_query


async def validate_graph() -> dict[str, Any]:
    """Validate the graph and return a report with errors and suggestions.

    Checks:
    - Every Device vertex has required properties per its ontology class.
    - Capability consistency (Dimmable requires brightness parameter).
    - Relationship cardinality (MotionSensor monitors exactly one room).
    """
    errors: list[dict] = []

    # Check 1: Devices missing ha_entity_id or ip_address
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device').or(__.hasNot('ha_entity_id'), __.hasNot('ip_address'))"
            ".valueMap('name', 'device_type')"
        ),
    )
    for device in result.get("result", []):
        name = device.get("name", ["unknown"])[0]
        errors.append(
            {
                "type": "MISSING_PROPERTY",
                "vertex": name,
                "detail": "Device is missing ha_entity_id or ip_address",
                "suggestion": "Populate device metadata from Home Assistant",
            }
        )

    # Check 2: Dimmable devices must have brightness parameter
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device').out('HAS_CAPABILITY').has('name', 'Dimmable')"
            ".in('HAS_CAPABILITY').hasNot('brightness').values('name')"
        ),
    )
    for name in result.get("result", []):
        errors.append(
            {
                "type": "CAPABILITY_CONSISTENCY",
                "vertex": name,
                "detail": "Dimmable device missing brightness property",
                "suggestion": "Add brightness property or remove Dimmable capability",
            }
        )

    # Check 3: MotionSensor must monitor exactly one room
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device').has('device_type', 'MotionSensor')"
            ".as('sensor').out('MONITORS').count().as('count')"
            ".select('sensor','count').by(values('name')).by()"
        ),
    )
    for item in result.get("result", []):
        if isinstance(item, dict):
            count = item.get("count", 0)
            name = item.get("sensor", "unknown")
            if count != 1:
                errors.append(
                    {
                        "type": "CARDINALITY",
                        "vertex": name,
                        "detail": f"MotionSensor monitors {count} rooms (expected exactly 1)",
                        "suggestion": "Ensure each MotionSensor has exactly one MONITORS edge",
                    }
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "error_count": len(errors),
    }
