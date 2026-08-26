"""Sync existing MySQL data into ArcadeDB."""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import (
    Device,
    DeviceType,
    HomeAssistantDomain,
    Room,
    RoomType,
    SensorReading,
    device_type_for_ha_domain,
)

logger = logging.getLogger(__name__)

ConflictPolicy = Literal["update", "skip"]


async def sync_rooms_to_graph(
    mysql_session: AsyncSession,
    conflict_policy: ConflictPolicy = "update",
) -> dict[int, str]:
    """Sync MySQL rooms into ArcadeDB as Room vertices.

    Returns a mapping of MySQL room_id -> ArcadeDB RID when ArcadeDB returns RIDs.
    """
    result = await mysql_session.execute(text("SELECT id, name FROM rooms"))
    rows = _rows_as_dicts(result.mappings().all())
    commands = [_room_command(row, conflict_policy) for row in rows]
    responses = await _execute_transaction(commands)
    return _rid_map_from_rows(rows, responses)


async def sync_devices_to_graph(
    mysql_session: AsyncSession,
    room_rid_map: dict[int, str] | None = None,
    conflict_policy: ConflictPolicy = "update",
) -> dict[int, str]:
    """Sync MySQL devices into ArcadeDB as Device vertices.

    The room map is accepted for compatibility with earlier callers; current sync
    uses stable room names so idempotent edge repair works even when RIDs are not
    returned by ArcadeDB.
    """
    result = await mysql_session.execute(_devices_select())
    rows = _rows_as_dicts(result.mappings().all())
    commands: list[str] = []
    for row in rows:
        commands.append(_device_command(row, conflict_policy))
        commands.extend(_device_room_edge_commands(row))
    responses = await _execute_transaction(commands)
    device_responses = [
        response
        for command, response in zip(commands, responses)
        if _is_device_vertex_command(command)
    ]
    return _rid_map_from_rows(rows, device_responses)


async def sync_sensor_readings_to_graph(
    mysql_session: AsyncSession,
    last_sync: str = "1970-01-01 00:00:00",
    conflict_policy: ConflictPolicy = "update",
) -> dict[str, Any]:
    """Sync changed MySQL sensor readings into ArcadeDB."""
    result = await mysql_session.execute(
        text(
            "SELECT id, device_id, room_id, timestamp, data "
            "FROM sensor_readings "
            "WHERE timestamp >= :last_sync "
            "ORDER BY timestamp ASC"
        ),
        {"last_sync": last_sync},
    )
    rows = _rows_as_dicts(result.mappings().all())
    commands = [_sensor_reading_command(row, conflict_policy) for row in rows]
    await _execute_transaction(commands)
    return {"changed_sensor_readings": len(rows), "last_sync": last_sync}


async def incremental_sync(
    mysql_session: AsyncSession,
    last_sync: str = "1970-01-01 00:00:00",
    conflict_policy: ConflictPolicy = "update",
) -> dict[str, Any]:
    """Idempotently sync current MySQL graph records into ArcadeDB.

    Rooms and devices are upserted because the current MySQL schema does not
    expose updated_at columns for them. Sensor readings are filtered by timestamp
    for incremental sync.
    """
    room_result = await mysql_session.execute(
        text("SELECT id, name, description, ha_area_id FROM rooms"),
    )
    changed_rooms = _rows_as_dicts(room_result.mappings().all())

    device_result = await mysql_session.execute(_devices_select())
    changed_devices = _rows_as_dicts(device_result.mappings().all())

    reading_result = await mysql_session.execute(
        text(
            "SELECT id, device_id, room_id, timestamp, data "
            "FROM sensor_readings "
            "WHERE timestamp >= :last_sync "
            "ORDER BY timestamp ASC"
        ),
        {"last_sync": last_sync},
    )
    changed_readings = _rows_as_dicts(reading_result.mappings().all())

    commands: list[str] = []
    commands.extend(_room_command(row, conflict_policy) for row in changed_rooms)
    for row in changed_devices:
        commands.append(_device_command(row, conflict_policy))
        commands.extend(_device_room_edge_commands(row))
    commands.extend(
        _sensor_reading_command(row, conflict_policy) for row in changed_readings
    )

    await _execute_transaction(commands)

    return {
        "changed_rooms": len(changed_rooms),
        "changed_devices": len(changed_devices),
        "changed_sensor_readings": len(changed_readings),
        "last_sync": last_sync,
        "conflict_policy": conflict_policy,
    }


async def grant_access_to_graph(
    mysql_session: AsyncSession,
    user_id: int,
    room_id: int | None = None,
    device_id: int | None = None,
    permission: str | None = None,
    allowed_start_hour: int | None = None,
    allowed_end_hour: int | None = None,
) -> None:
    """Create HAS_ACCESS edges in ArcadeDB for room/device grants."""
    user_result = await mysql_session.execute(
        text("SELECT email FROM users WHERE id = :id"),
        {"id": user_id},
    )
    user = user_result.mappings().first()
    if user is None:
        return

    if room_id is not None:
        room_result = await mysql_session.execute(
            text("SELECT name FROM rooms WHERE id = :id"),
            {"id": room_id},
        )
        room = room_result.mappings().first()
        if room is not None:
            await _create_access_edge(
                user_email=user["email"],
                target_label="Room",
                target_property="name",
                target_value=room["name"],
                permission=permission or "room:read",
                allowed_start_hour=allowed_start_hour,
                allowed_end_hour=allowed_end_hour,
            )

    if device_id is not None:
        device_result = await mysql_session.execute(
            text("SELECT name FROM devices WHERE id = :id"),
            {"id": device_id},
        )
        device = device_result.mappings().first()
        if device is not None:
            await _create_access_edge(
                user_email=user["email"],
                target_label="Device",
                target_property="name",
                target_value=device["name"],
                permission=permission or "device:read",
                allowed_start_hour=allowed_start_hour,
                allowed_end_hour=allowed_end_hour,
            )


def _devices_select() -> Any:
    return text(
        "SELECT d.id, d.name, d.device_type, d.room_id, d.is_active, d.ha_entity_id, "
        "d.ha_device_id, d.ha_platform, d.manufacturer, d.model, d.ip_address, "
        "r.name AS room_name, r.ha_area_id "
        "FROM devices d LEFT JOIN rooms r ON d.room_id = r.id"
    )


def _rows_as_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _room_command(row: Mapping[str, Any], conflict_policy: ConflictPolicy) -> str:
    room = Room(
        name=row["name"],
        room_type=_infer_room_type(row["name"]),
        description=row.get("description"),
        ha_area_id=row.get("ha_area_id"),
    )
    fields = _assignments(
        {
            "mysql_id": int(row["id"]),
            "name": room.name,
            "room_type": room.room_type,
            "description": room.description,
            "ha_area_id": room.ha_area_id,
            "created_at": "sysdate()",
        }
    )
    identity = (
        f"ha_area_id = {_sql_value(str(room.ha_area_id))}"
        if room.ha_area_id
        else f"mysql_id = {int(row['id'])}"
    )
    if conflict_policy == "skip":
        return (
            f"CREATE VERTEX Room SET {fields} "
            f"IF NOT EXISTS WHERE {identity}"
        )
    return f"UPDATE Room SET {fields} UPSERT WHERE {identity}"


def _device_command(row: Mapping[str, Any], conflict_policy: ConflictPolicy) -> str:
    ha_domain = _domain_from_entity_id(row.get("ha_entity_id"))
    device_type = _device_type_from_row(row, ha_domain)
    device = Device(
        name=row["name"],
        device_type=device_type,
        ha_domain=ha_domain,
        ha_entity_id=row.get("ha_entity_id"),
        ha_device_id=row.get("ha_device_id"),
        ha_area_id=row.get("ha_area_id"),
        ha_platform=row.get("ha_platform"),
        manufacturer=row.get("manufacturer"),
        model=row.get("model"),
        ip_address=row.get("ip_address"),
        is_active=bool(row["is_active"]),
    )
    fields = _assignments(
        {
            "mysql_id": int(row["id"]),
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
            "is_active": device.is_active,
            "created_at": "sysdate()",
        }
    )
    identity = (
        f"ha_entity_id = {_sql_value(str(device.ha_entity_id))}"
        if device.ha_entity_id
        else f"mysql_id = {int(row['id'])}"
    )
    if conflict_policy == "skip":
        return (
            f"CREATE VERTEX Device SET {fields} "
            f"IF NOT EXISTS WHERE {identity}"
        )
    return f"UPDATE Device SET {fields} UPSERT WHERE {identity}"


def _device_room_edge_commands(row: Mapping[str, Any]) -> list[str]:
    if not row.get("room_id") or not row.get("room_name"):
        return []
    device_selector = f"(SELECT FROM Device WHERE mysql_id = {int(row['id'])})"
    room_selector = f"(SELECT FROM Room WHERE mysql_id = {int(row['room_id'])})"
    return [
        f"DELETE EDGE LOCATED_IN FROM {device_selector} TO {room_selector}",
        f"CREATE EDGE LOCATED_IN FROM {device_selector} TO {room_selector}",
    ]


def _sensor_reading_command(
    row: Mapping[str, Any],
    conflict_policy: ConflictPolicy,
) -> str:
    reading = SensorReading(
        mysql_id=int(row["id"]),
        device_id=int(row["device_id"]),
        room_id=int(row["room_id"]),
        timestamp=row["timestamp"],
        data=_json_object(row.get("data")),
    )
    fields = [
        f"mysql_id = {reading.mysql_id}",
        f"device_id = {reading.device_id}",
        f"room_id = {reading.room_id}",
        f"timestamp = {_sql_datetime(reading.timestamp)}",
        f"data = {_sql_json(reading.data)}",
    ]
    if conflict_policy == "skip":
        return (
            f"CREATE VERTEX SensorReading SET {', '.join(fields)} "
            f"IF NOT EXISTS WHERE mysql_id = {reading.mysql_id}"
        )
    return (
        f"UPDATE SensorReading SET {', '.join(fields)} "
        f"UPSERT WHERE mysql_id = {reading.mysql_id}"
    )


async def _create_access_edge(
    user_email: str,
    target_label: str,
    target_property: str,
    target_value: str,
    permission: str,
    allowed_start_hour: int | None,
    allowed_end_hour: int | None,
) -> None:
    fields = [
        f"permission = {_sql_string(permission)}",
        "created_at = datetime()",
    ]
    if allowed_start_hour is not None:
        fields.append(f"allowed_start_hour = {allowed_start_hour}")
    if allowed_end_hour is not None:
        fields.append(f"allowed_end_hour = {allowed_end_hour}")

    cmd = (
        "CREATE EDGE HAS_ACCESS "
        f"FROM (SELECT FROM User WHERE email = {_sql_string(user_email)}) "
        f"TO (SELECT FROM {target_label} WHERE {target_property} = {_sql_string(target_value)}) "
        f"SET {', '.join(fields)}"
    )
    await arcadedb_query("sql", cmd, readonly=False)


async def _execute_transaction(commands: Sequence[str]) -> list[dict]:
    """Execute idempotent ArcadeDB upserts.

    HTTP commands use independent requests, so server-side BEGIN/COMMIT cannot
    span them reliably. Every sync command is an idempotent upsert or edge
    repair; execute each request directly and surface the exact failure.
    """
    if not commands:
        return []

    responses: list[dict] = []
    for command in commands:
        try:
            responses.append(await arcadedb_query("sql", command, readonly=False))
        except Exception:
            logger.exception("ArcadeDB graph sync command failed: %s", command)
            raise
    return responses


def _rid_map_from_rows(
    rows: Sequence[Mapping[str, Any]], responses: Sequence[dict]
) -> dict[int, str]:
    rid_map: dict[int, str] = {}
    for row, response in zip(rows, responses):
        rid = _extract_rid(response)
        if rid:
            rid_map[int(row["id"])] = rid
    return rid_map


def _is_device_vertex_command(command: str) -> bool:
    return command.startswith("UPDATE Device") or command.startswith(
        "CREATE VERTEX Device"
    )


def _extract_rid(response: dict) -> str | None:
    """Extract the @rid from an ArcadeDB CREATE/UPDATE response."""
    results = response.get("result", [])
    if results and isinstance(results[0], dict):
        return results[0].get("@rid")
    return None


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
    if "yard" in normalized or "lawn" in normalized or "outdoor" in normalized:
        return RoomType.OUTDOOR
    return RoomType.LIVING_ROOM


def _domain_from_entity_id(entity_id: str | None) -> HomeAssistantDomain | None:
    if not entity_id or "." not in entity_id:
        return None
    domain = entity_id.split(".", maxsplit=1)[0]
    try:
        return HomeAssistantDomain(domain)
    except ValueError:
        return None


def _device_type_from_row(
    row: Mapping[str, Any],
    ha_domain: HomeAssistantDomain | None,
) -> DeviceType:
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
        "other": DeviceType.OTHER,
    }
    device_type = type_map.get(str(row["device_type"]), DeviceType.OTHER)
    if device_type == DeviceType.OTHER and ha_domain is not None:
        return device_type_for_ha_domain(ha_domain)
    return device_type


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": value}


def _sql_datetime(value: datetime) -> str:
    return _sql_string(value.isoformat(sep=" "))


def _sql_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _assignments(fields: Mapping[str, Any]) -> str:
    return ", ".join(
        f"{name} = {_sql_value(value)}"
        for name, value in fields.items()
        if _has_value(value)
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _sql_value(value: Any) -> str:
    if value == "sysdate()":
        return "sysdate()"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return _sql_string(str(value))


def _sql_string(value: str) -> str:
    """Return a single-quoted SQL literal for ArcadeDB commands."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
