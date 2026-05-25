"""Validate ArcadeDB graph consistency against the EcoNest ontology."""

from typing import Any

from pydantic import BaseModel, Field

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import CapabilityName, DeviceType, RoomType


class ValidationErrorDetail(BaseModel):
    """A graph validation error with an actionable suggestion."""

    type: str
    vertex: str
    detail: str
    suggestion: str
    severity: str = "error"


class ValidationReport(BaseModel):
    """Structured validation result returned by the ontology API."""

    valid: bool
    errors: list[ValidationErrorDetail] = Field(default_factory=list)
    error_count: int


async def validate_graph() -> dict[str, Any]:
    """Validate the graph and return errors with repair suggestions.

    The checks mirror the current EcoNest graph schema and seed data:
    - Device and Room vertices must have core fields and known enum values.
    - Active devices should be placed in exactly one room.
    - Capabilities must be known ontology capabilities.
    - Dimmable devices must carry a brightness property.
    - Motion and sound sensor metadata must monitor exactly one room.
    """
    errors: list[ValidationErrorDetail] = []
    errors.extend(await _validate_devices())
    errors.extend(await _validate_rooms())
    errors.extend(await _validate_capabilities())
    errors.extend(await _validate_dimmable_devices())
    errors.extend(await _validate_sensor_monitor_cardinality())

    report = ValidationReport(
        valid=not errors,
        errors=errors,
        error_count=len(errors),
    )
    return report.model_dump()


async def _validate_devices() -> list[ValidationErrorDetail]:
    errors: list[ValidationErrorDetail] = []

    missing_result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device')"
            ".or(hasNot('name'), hasNot('device_type'), hasNot('is_active'))"
            ".valueMap(true)"
        ),
    )
    for device in _result_dicts(missing_result):
        name = _name(device)
        missing = _missing_fields(device, ["name", "device_type", "is_active"])
        errors.append(
            ValidationErrorDetail(
                type="MISSING_DEVICE_PROPERTY",
                vertex=name,
                detail=f"Device is missing required field(s): {', '.join(missing)}",
                suggestion="Re-run graph seeding or sync the device with name, device_type, and is_active.",
            )
        )

    all_result = await arcadedb_query(
        "gremlin",
        "g.V().hasLabel('Device').valueMap(true)",
    )
    known_device_types = {item.value for item in DeviceType}
    for device in _result_dicts(all_result):
        device_type = _first_string(device.get("device_type"))
        if device_type and device_type not in known_device_types:
            errors.append(
                ValidationErrorDetail(
                    type="UNKNOWN_DEVICE_TYPE",
                    vertex=_name(device),
                    detail=f"Device has unknown device_type '{device_type}'.",
                    suggestion="Map the source device type to a DeviceType enum value before syncing.",
                )
            )

    placement_result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device')"
            ".has('is_active', true)"
            ".as('device')"
            ".project('device', 'room_count')"
            ".by(valueMap(true))"
            ".by(out('LOCATED_IN').hasLabel('Room').count())"
        ),
    )
    for row in _result_dicts(placement_result):
        device = _nested_dict(row.get("device"))
        room_count = _int_value(row.get("room_count"))
        if room_count != 1:
            errors.append(
                ValidationErrorDetail(
                    type="DEVICE_ROOM_CARDINALITY",
                    vertex=_name(device),
                    detail=f"Active device is located in {room_count} rooms; expected exactly 1.",
                    suggestion="Repair LOCATED_IN edges from the device to its MySQL/Home Assistant room.",
                )
            )

    return errors


async def _validate_rooms() -> list[ValidationErrorDetail]:
    errors: list[ValidationErrorDetail] = []
    result = await arcadedb_query(
        "gremlin",
        "g.V().hasLabel('Room').valueMap(true)",
    )
    known_room_types = {item.value for item in RoomType}
    for room in _result_dicts(result):
        missing = _missing_fields(room, ["name", "room_type"])
        if missing:
            errors.append(
                ValidationErrorDetail(
                    type="MISSING_ROOM_PROPERTY",
                    vertex=_name(room),
                    detail=f"Room is missing required field(s): {', '.join(missing)}",
                    suggestion="Re-run graph seeding or sync the room with name and room_type.",
                )
            )
            continue

        room_type = _first_string(room.get("room_type"))
        if room_type and room_type not in known_room_types:
            errors.append(
                ValidationErrorDetail(
                    type="UNKNOWN_ROOM_TYPE",
                    vertex=_name(room),
                    detail=f"Room has unknown room_type '{room_type}'.",
                    suggestion="Map the room name to a RoomType enum value before syncing.",
                )
            )
    return errors


async def _validate_capabilities() -> list[ValidationErrorDetail]:
    result = await arcadedb_query(
        "gremlin",
        "g.V().hasLabel('Capability').valueMap(true)",
    )
    known_capabilities = {item.value for item in CapabilityName}
    errors: list[ValidationErrorDetail] = []
    for capability in _result_dicts(result):
        name = _first_string(capability.get("name"))
        if name and name not in known_capabilities:
            errors.append(
                ValidationErrorDetail(
                    type="UNKNOWN_CAPABILITY",
                    vertex=name,
                    detail=f"Capability '{name}' is not defined in the EcoNest ontology.",
                    suggestion="Use a CapabilityName enum value or update the ontology and reasoner rules together.",
                )
            )
    return errors


async def _validate_dimmable_devices() -> list[ValidationErrorDetail]:
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device')"
            ".where(out('HAS_CAPABILITY').has('name', 'Dimmable'))"
            ".hasNot('brightness')"
            ".valueMap(true)"
        ),
    )
    return [
        ValidationErrorDetail(
            type="CAPABILITY_CONSISTENCY",
            vertex=_name(device),
            detail="Dimmable device is missing the brightness property.",
            suggestion="Populate Device.brightness when Dimmable is inferred, or remove the Dimmable capability.",
        )
        for device in _result_dicts(result)
    ]


async def _validate_sensor_monitor_cardinality() -> list[ValidationErrorDetail]:
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Sensor')"
            ".has('sensor_type', within('motion', 'sound'))"
            ".as('sensor')"
            ".project('sensor', 'room_count')"
            ".by(valueMap(true))"
            ".by(out('MONITORS').hasLabel('Room').count())"
        ),
    )
    errors: list[ValidationErrorDetail] = []
    for row in _result_dicts(result):
        sensor = _nested_dict(row.get("sensor"))
        room_count = _int_value(row.get("room_count"))
        if room_count != 1:
            errors.append(
                ValidationErrorDetail(
                    type="MONITORS_CARDINALITY",
                    vertex=_name(sensor),
                    detail=f"Sensor monitors {room_count} rooms; expected exactly 1.",
                    suggestion="Run the ontology reasoner or repair MONITORS edges from the Sensor to one Room.",
                )
            )
    return errors


def _result_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("result", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _missing_fields(row: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if _first_value(row.get(field)) is None]


def _name(row: dict[str, Any]) -> str:
    return _first_string(row.get("name")) or _first_string(row.get("@rid")) or "unknown"


def _first_string(value: Any) -> str | None:
    first = _first_value(value)
    return first if isinstance(first, str) and first else None


def _first_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _int_value(value: Any) -> int:
    first = _first_value(value)
    try:
        return int(first)
    except (TypeError, ValueError):
        return 0
