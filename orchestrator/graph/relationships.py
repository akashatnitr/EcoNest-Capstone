"""Infer and repair higher-level ArcadeDB graph relationships."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import ActionName, CapabilityName, DeviceType


class RelationshipSyncResult(BaseModel):
    """Counts of graph relationship sync work."""

    users: int = 0
    capabilities: int = 0
    actions: int = 0
    circuits: int = 0
    has_capability: int = 0
    requires_capability: int = 0
    powered_by: int = 0
    depends_on: int = 0
    derived_from: int = 0
    owns: int = 0
    can_perform: int = 0


DEVICE_CAPABILITIES: dict[str, tuple[CapabilityName, ...]] = {
    DeviceType.ENERGY_MONITOR.value: (CapabilityName.POWER_MONITORING,),
    DeviceType.SMART_PLUG.value: (
        CapabilityName.ON_OFF,
        CapabilityName.POWER_MONITORING,
    ),
    DeviceType.SMART_BULB.value: (
        CapabilityName.ON_OFF,
        CapabilityName.DIMMABLE,
        CapabilityName.COLOR_CONTROL,
    ),
    DeviceType.MOTION_SENSOR.value: (CapabilityName.MOTION_DETECTION,),
    DeviceType.SOUND_SENSOR.value: (CapabilityName.SOUND_DETECTION,),
    DeviceType.THERMOSTAT.value: (CapabilityName.TEMPERATURE_CONTROL,),
    DeviceType.CLIMATE.value: (CapabilityName.TEMPERATURE_CONTROL,),
    DeviceType.SMART_SWITCH.value: (CapabilityName.ON_OFF,),
    DeviceType.COVER.value: (CapabilityName.COVER_CONTROL,),
    DeviceType.VALVE.value: (CapabilityName.WATER_CONTROL,),
    DeviceType.FAN.value: (CapabilityName.ON_OFF,),
    DeviceType.MEDIA_PLAYER.value: (CapabilityName.ON_OFF,),
}

ACTION_REQUIREMENTS: dict[ActionName, CapabilityName] = {
    ActionName.TURN_ON: CapabilityName.ON_OFF,
    ActionName.TURN_OFF: CapabilityName.ON_OFF,
    ActionName.SET_BRIGHTNESS: CapabilityName.DIMMABLE,
    ActionName.SET_COLOR_TEMP: CapabilityName.COLOR_CONTROL,
    ActionName.SET_TEMPERATURE: CapabilityName.TEMPERATURE_CONTROL,
    ActionName.OPEN: CapabilityName.COVER_CONTROL,
    ActionName.CLOSE: CapabilityName.COVER_CONTROL,
    ActionName.READ_STATE: CapabilityName.ON_OFF,
}

ROLE_ACTIONS: dict[str, tuple[ActionName, ...]] = {
    "guest": (ActionName.READ_STATE,),
    "family_member": (
        ActionName.READ_STATE,
        ActionName.TURN_ON,
        ActionName.TURN_OFF,
        ActionName.SET_BRIGHTNESS,
    ),
    "homeowner": tuple(ActionName),
    "superadmin": tuple(ActionName),
    "service_account": (ActionName.READ_STATE,),
}


async def sync_graph_relationships(
    mysql_session: AsyncSession | None = None,
) -> RelationshipSyncResult:
    """Populate graph edges that can be inferred from current graph/MySQL data."""
    result = RelationshipSyncResult()

    await _ensure_relationship_indexes()

    if mysql_session is not None:
        result.users = await _sync_users(mysql_session)

    result.capabilities = await _ensure_capabilities()
    result.actions = await _ensure_actions()
    result.requires_capability = await _sync_action_capability_edges()
    result.has_capability = await _sync_device_capability_edges()
    result.circuits, result.powered_by = await _sync_room_circuit_edges()
    result.depends_on = await _sync_device_dependency_edges()
    result.derived_from = await _sync_observation_sensor_edges()
    result.owns = await _sync_user_home_edges()
    result.can_perform = await _sync_user_action_edges()

    return result


async def _ensure_relationship_indexes() -> None:
    for command in (
        "CREATE INDEX IF NOT EXISTS ON Capability(name) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Action(name) UNIQUE",
        "CREATE INDEX IF NOT EXISTS ON Circuit(breaker_id) UNIQUE",
    ):
        await arcadedb_query("sql", command, readonly=False)


async def _sync_users(mysql_session: AsyncSession) -> int:
    rows = await _mysql_rows(
        mysql_session,
        "SELECT email, role, household_id, is_active FROM users WHERE is_active = TRUE",
    )
    for row in rows:
        fields = _assignments(
            {
                "email": row.get("email"),
                "role": row.get("role"),
                "household_id": row.get("household_id"),
                "is_active": bool(row.get("is_active", True)),
                "created_at": _SqlRaw(_sql_now()),
            }
        )
        await arcadedb_query(
            "sql",
            f"UPDATE User SET {fields} UPSERT WHERE email = {_sql_value(row.get('email'))}",
            readonly=False,
        )
    return len(rows)


async def _ensure_capabilities() -> int:
    for capability in CapabilityName:
        await arcadedb_query(
            "sql",
            (
                "UPDATE Capability SET "
                f"name = {_sql_value(capability.value)}, "
                f"description = {_sql_value(f'{capability.value} capability')} "
                f"UPSERT WHERE name = {_sql_value(capability.value)}"
            ),
            readonly=False,
        )
    return len(CapabilityName)


async def _ensure_actions() -> int:
    for action in ActionName:
        await arcadedb_query(
            "sql",
            (
                "UPDATE Action SET "
                f"name = {_sql_value(action.value)}, "
                "parameters = {}, "
                f"timestamp = {_sql_now()} "
                f"UPSERT WHERE name = {_sql_value(action.value)}"
            ),
            readonly=False,
        )
    return len(ActionName)


async def _sync_action_capability_edges() -> int:
    await _clear_edges("REQUIRES_CAPABILITY")
    count = 0
    for action, capability in ACTION_REQUIREMENTS.items():
        await _repair_edge(
            "REQUIRES_CAPABILITY",
            _selector("Action", "name", action.value),
            _selector("Capability", "name", capability.value),
        )
        count += 1
    return count


async def _sync_device_capability_edges() -> int:
    await _clear_edges("HAS_CAPABILITY")
    count = 0
    for device in await _graph_rows("g.V().hasLabel('Device').valueMap(true)"):
        device_rid = _rid(device)
        device_type = _first_string(device.get("device_type"))
        if not device_rid or not device_type:
            continue
        for capability in DEVICE_CAPABILITIES.get(device_type, ()):
            await _repair_edge(
                "HAS_CAPABILITY",
                device_rid,
                _selector("Capability", "name", capability.value),
            )
            count += 1
    return count


async def _sync_room_circuit_edges() -> tuple[int, int]:
    await _clear_edges("POWERED_BY")
    circuits = 0
    powered_by = 0
    for room in await _graph_rows("g.V().hasLabel('Room').valueMap(true)"):
        room_rid = _rid(room)
        room_name = _first_string(room.get("name"))
        area_id = _first_string(room.get("ha_area_id"))
        if not room_rid or not room_name or area_id == "home_assistant":
            continue

        breaker_id = f"room:{area_id or _slug(room_name)}"
        await arcadedb_query(
            "sql",
            (
                "UPDATE Circuit SET "
                f"name = {_sql_value(f'{room_name} Circuit')}, "
                f"breaker_id = {_sql_value(breaker_id)}, "
                f"created_at = {_sql_now()} "
                f"UPSERT WHERE breaker_id = {_sql_value(breaker_id)}"
            ),
            readonly=False,
        )
        circuits += 1

        device_result = await arcadedb_query(
            "gremlin",
            f"g.V('{_escape_gremlin(room_rid)}').in('LOCATED_IN').hasLabel('Device').valueMap(true)",
        )
        for device in _result_values(device_result):
            if not isinstance(device, Mapping):
                continue
            device_rid = _rid(device)
            if not device_rid:
                continue
            await _repair_edge(
                "POWERED_BY",
                device_rid,
                _selector("Circuit", "breaker_id", breaker_id),
            )
            powered_by += 1
    return circuits, powered_by


async def _sync_device_dependency_edges() -> int:
    await _clear_edges("DEPENDS_ON")
    count = 0
    for device in await _graph_rows("g.V().hasLabel('Device').valueMap(true)"):
        device_rid = _rid(device)
        via_device_id = _first_string(device.get("via_device_id"))
        if not device_rid or not via_device_id:
            continue
        parent_selector = (
            f"(SELECT FROM Device WHERE ha_device_id = {_sql_value(via_device_id)} LIMIT 1)"
        )
        if not await _selector_exists(parent_selector):
            continue
        await _repair_edge("DEPENDS_ON", device_rid, parent_selector)
        count += 1
    return count


async def _sync_observation_sensor_edges() -> int:
    await _clear_edges("DERIVED_FROM")
    count = 0
    for sensor in await _graph_rows("g.V().hasLabel('Sensor').valueMap(true)"):
        sensor_rid = _rid(sensor)
        entity_id = _first_string(sensor.get("ha_entity_id"))
        if not sensor_rid or not entity_id:
            continue
        observation_selector = (
            "(SELECT FROM Observation WHERE observation_type = 'ha_state' "
            f"AND source_sensor = {_sql_value(entity_id)})"
        )
        if not await _selector_exists(observation_selector):
            continue
        await _repair_edge("DERIVED_FROM", observation_selector, sensor_rid)
        count += 1
    return count


async def _sync_user_home_edges() -> int:
    await _clear_edges("OWNS")
    user_result = await arcadedb_query(
        "sql",
        "SELECT @rid FROM User WHERE role IN ['homeowner', 'superadmin']",
    )
    users = _result_values(user_result)
    home_selector = "(SELECT FROM Home LIMIT 1)"
    if not users or not await _selector_exists(home_selector):
        return 0
    count = 0
    for user in users:
        if not isinstance(user, Mapping):
            continue
        user_rid = user.get("@rid")
        if not isinstance(user_rid, str):
            continue
        await _repair_edge("OWNS", user_rid, home_selector)
        count += 1
    return count


async def _sync_user_action_edges() -> int:
    await _clear_edges("CAN_PERFORM")
    count = 0
    for user in await _graph_rows("g.V().hasLabel('User').valueMap(true)"):
        user_rid = _rid(user)
        role = _first_string(user.get("role"))
        if not user_rid or not role:
            continue
        for action in ROLE_ACTIONS.get(role, ()):
            await _repair_edge(
                "CAN_PERFORM",
                user_rid,
                _selector("Action", "name", action.value),
            )
            count += 1
    return count


async def _mysql_rows(mysql_session: AsyncSession, sql: str) -> list[dict[str, Any]]:
    rows = (await mysql_session.execute(text(sql))).mappings().all()
    return [dict(row) for row in rows]


async def _graph_rows(query: str) -> list[Any]:
    result = await arcadedb_query("gremlin", query)
    return _result_values(result)


async def _selector_exists(selector: str) -> bool:
    result = await arcadedb_query("sql", f"SELECT FROM {selector} LIMIT 1")
    rows = result.get("result", [])
    return isinstance(rows, list) and bool(rows)


async def _repair_edge(
    edge_label: str,
    from_selector: str,
    to_selector: str,
) -> None:
    await arcadedb_query(
        "sql",
        (
            f"DELETE FROM {edge_label} "
            f"WHERE {_edge_endpoint_condition('@out', from_selector)} "
            f"AND {_edge_endpoint_condition('@in', to_selector)}"
        ),
        readonly=False,
    )
    await arcadedb_query(
        "sql",
        (
            f"CREATE EDGE {edge_label} FROM {from_selector} TO {to_selector} "
            f"IF NOT EXISTS SET created_at = {_sql_now()}"
        ),
        readonly=False,
    )


async def _clear_edges(edge_label: str) -> None:
    await arcadedb_query("sql", f"DELETE FROM {edge_label} WHERE true", readonly=False)


def _edge_endpoint_condition(field: str, selector: str) -> str:
    if selector.startswith("#"):
        return f"{field} = {selector}"
    return f"{field} IN {selector}"


def _result_values(result: dict[str, Any]) -> list[Any]:
    rows = result.get("result", [])
    return rows if isinstance(rows, list) else [rows]


def _rid(row: Mapping[str, Any]) -> str | None:
    for key in ("@rid", "id", "rid"):
        value = row.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return None


def _first_string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _selector(label: str, key_name: str, key_value: Any) -> str:
    return f"(SELECT FROM {label} WHERE {key_name} = {_sql_value(key_value)})"


def _assignments(fields: Mapping[str, Any]) -> str:
    return ", ".join(
        f"{name} = {_sql_value(value)}"
        for name, value in fields.items()
        if value is not None and value != ""
    )


class _SqlRaw:
    def __init__(self, value: str) -> None:
        self.value = value


def _sql_value(value: Any) -> str:
    if isinstance(value, _SqlRaw):
        return value.value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _sql_now() -> str:
    return _sql_value(datetime.now(UTC).isoformat(sep=" "))


def _escape_gremlin(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
