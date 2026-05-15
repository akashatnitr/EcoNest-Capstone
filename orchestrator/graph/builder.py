"""Sync existing MySQL data into ArcadeDB."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import Device, Room


async def sync_rooms_to_graph(mysql_session: AsyncSession) -> dict[int, str]:
    """Sync MySQL rooms into ArcadeDB as Room vertices.

    Returns a mapping of MySQL room_id -> ArcadeDB RID.
    """
    result = await mysql_session.execute(
        text("SELECT id, name, description FROM rooms")
    )
    rows = result.mappings().all()
    rid_map: dict[int, str] = {}

    for row in rows:
        room = Room(
            name=row["name"], room_type="LivingRoom", description=row["description"]
        )
        cmd = (
            f"CREATE VERTEX Room SET name = '{room.name}', "
            f"room_type = '{room.room_type}', "
            f"description = '{room.description or ''}', "
            f"created_at = datetime()"
        )
        resp = await arcadedb_query("sql", cmd, readonly=False)
        rid = _extract_rid(resp)
        if rid:
            rid_map[row["id"]] = rid
    return rid_map


async def sync_devices_to_graph(
    mysql_session: AsyncSession, room_rid_map: dict[int, str]
) -> dict[int, str]:
    """Sync MySQL devices into ArcadeDB as Device vertices.

    Returns a mapping of MySQL device_id -> ArcadeDB RID.
    """
    result = await mysql_session.execute(
        text(
            "SELECT id, name, ip_address, device_type, room_id, is_active FROM devices"
        )
    )
    rows = result.mappings().all()
    rid_map: dict[int, str] = {}

    type_map = {
        "smart_plug": "SmartPlug",
        "motion_sensor": "MotionSensor",
        "sound_sensor": "SoundSensor",
        "other": "SmartSwitch",
    }

    for row in rows:
        device = Device(
            name=row["name"],
            device_type=type_map.get(row["device_type"], "SmartSwitch"),
            ip_address=row["ip_address"],
            is_active=bool(row["is_active"]),
        )
        cmd = (
            f"CREATE VERTEX Device SET name = '{device.name}', "
            f"device_type = '{device.device_type}', "
            f"ip_address = '{device.ip_address or ''}', "
            f"is_active = {str(device.is_active).lower()}, "
            f"created_at = datetime()"
        )
        resp = await arcadedb_query("sql", cmd, readonly=False)
        rid = _extract_rid(resp)
        if rid:
            rid_map[row["id"]] = rid

        # Link device to room
        room_id = row["room_id"]
        if room_id and room_id in room_rid_map and rid:
            await arcadedb_query(
                "sql",
                f"CREATE EDGE LOCATED_IN FROM {rid} TO {room_rid_map[room_id]}",
                readonly=False,
            )
    return rid_map


async def incremental_sync(
    mysql_session: AsyncSession,
    last_sync: str = "1970-01-01 00:00:00",
) -> dict[str, Any]:
    """Idempotently sync current MySQL room/device records into ArcadeDB.

    Returns summary statistics.
    """
    room_result = await mysql_session.execute(
        text("SELECT id, name, description FROM rooms"),
    )
    changed_rooms = room_result.mappings().all()

    device_result = await mysql_session.execute(
        text(
            "SELECT d.id, d.name, d.ip_address, d.device_type, d.room_id, "
            "d.is_active, r.name AS room_name "
            "FROM devices d LEFT JOIN rooms r ON d.room_id = r.id"
        ),
    )
    changed_devices = device_result.mappings().all()

    for row in changed_rooms:
        await _upsert_room(row)
    for row in changed_devices:
        await _upsert_device(row)

    return {
        "changed_rooms": len(changed_rooms),
        "changed_devices": len(changed_devices),
        "last_sync": last_sync,
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


async def _upsert_room(row: Any) -> None:
    cmd = (
        "UPDATE Room SET "
        f"name = {_sql_string(row['name'])}, "
        "room_type = 'LivingRoom', "
        f"description = {_sql_string(row['description'] or '')}, "
        "created_at = datetime() "
        f"UPSERT WHERE name = {_sql_string(row['name'])}"
    )
    await arcadedb_query("sql", cmd, readonly=False)


async def _upsert_device(row: Any) -> None:
    type_map = {
        "smart_plug": "SmartPlug",
        "motion_sensor": "MotionSensor",
        "sound_sensor": "SoundSensor",
        "light": "SmartBulb",
        "switch": "SmartSwitch",
        "climate": "Thermostat",
        "other": "SmartSwitch",
    }
    device_type = type_map.get(row["device_type"], "SmartSwitch")
    cmd = (
        "UPDATE Device SET "
        f"name = {_sql_string(row['name'])}, "
        f"device_type = {_sql_string(device_type)}, "
        f"ip_address = {_sql_string(row['ip_address'] or '')}, "
        f"is_active = {str(bool(row['is_active'])).lower()}, "
        "created_at = datetime() "
        f"UPSERT WHERE name = {_sql_string(row['name'])}"
    )
    await arcadedb_query("sql", cmd, readonly=False)

    if row["room_id"] and row["room_name"]:
        delete_edge_cmd = (
            "DELETE EDGE LOCATED_IN "
            f"FROM (SELECT FROM Device WHERE name = {_sql_string(row['name'])}) "
            f"TO (SELECT FROM Room WHERE name = {_sql_string(row['room_name'])})"
        )
        await arcadedb_query("sql", delete_edge_cmd, readonly=False)
        edge_cmd = (
            "CREATE EDGE LOCATED_IN "
            f"FROM (SELECT FROM Device WHERE name = {_sql_string(row['name'])}) "
            f"TO (SELECT FROM Room WHERE name = {_sql_string(row['room_name'])})"
        )
        await arcadedb_query("sql", edge_cmd, readonly=False)


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


def _extract_rid(response: dict) -> str | None:
    """Extract the @rid from an ArcadeDB CREATE VERTEX response."""
    results = response.get("result", [])
    if results and isinstance(results[0], dict):
        return results[0].get("@rid")
    return None


def _sql_string(value: str) -> str:
    """Return a single-quoted SQL literal for ArcadeDB commands."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
