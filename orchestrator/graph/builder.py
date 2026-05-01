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
    """Perform incremental sync of changed records since last_sync.

    Returns summary statistics.
    """
    # Find rooms updated since last_sync
    room_result = await mysql_session.execute(
        text("SELECT id FROM rooms WHERE updated_at > :ts OR created_at > :ts"),
        {"ts": last_sync},
    )
    changed_rooms = room_result.scalars().all()

    # Find devices updated since last_sync
    device_result = await mysql_session.execute(
        text("SELECT id FROM devices WHERE updated_at > :ts OR created_at > :ts"),
        {"ts": last_sync},
    )
    changed_devices = device_result.scalars().all()

    # TODO: implement update logic for changed records
    return {
        "changed_rooms": len(changed_rooms),
        "changed_devices": len(changed_devices),
        "last_sync": last_sync,
    }


def _extract_rid(response: dict) -> str | None:
    """Extract the @rid from an ArcadeDB CREATE VERTEX response."""
    results = response.get("result", [])
    if results and isinstance(results[0], dict):
        return results[0].get("@rid")
    return None
