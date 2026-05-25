"""Forward-chaining ontology reasoner for the EcoNest ArcadeDB graph."""

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.core.database import arcadedb_query
from orchestrator.graph.models import ActionName, CapabilityName, DeviceType, RoomType

DEFAULT_RULES_PATH = Path(__file__).parent / "rules.json"


class CapabilityRule(BaseModel):
    """Infer capabilities for devices with a specific graph device_type."""

    device_type: DeviceType
    capabilities: list[CapabilityName] = Field(min_length=1)


class MonitorRule(BaseModel):
    """Infer Sensor nodes and MONITORS edges for sensor-like devices."""

    device_type: DeviceType
    sensor_type: str = Field(min_length=1)
    confidence_score: float = Field(default=0.9, ge=0, le=1)


class AccessRule(BaseModel):
    """Infer user action permissions from room access and room type."""

    role: str = Field(min_length=1)
    room_type: RoomType
    action: ActionName
    capability: CapabilityName | None = None


class ReasoningRules(BaseModel):
    """Configurable rule set for ontology reasoning."""

    capability_rules: list[CapabilityRule] = Field(default_factory=list)
    monitor_rules: list[MonitorRule] = Field(default_factory=list)
    access_rules: list[AccessRule] = Field(default_factory=list)


async def run_reasoner(rules_path: str | None = None) -> dict[str, Any]:
    """Run configured forward-chaining rules and return an inference summary."""
    rules = load_rules(rules_path)
    inferred: list[dict[str, Any]] = []

    for capability_rule in rules.capability_rules:
        inferred.extend(await _infer_capabilities(capability_rule))

    for monitor_rule in rules.monitor_rules:
        inferred.extend(await _infer_monitors(monitor_rule))

    for access_rule in rules.access_rules:
        inferred.extend(await _infer_can_perform(access_rule))

    return {
        "inferred": inferred,
        "total": len(inferred),
        "rules": {
            "capability_rules": len(rules.capability_rules),
            "monitor_rules": len(rules.monitor_rules),
            "access_rules": len(rules.access_rules),
        },
    }


def load_rules(rules_path: str | None = None) -> ReasoningRules:
    """Load a JSON reasoning rule set."""
    path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    return ReasoningRules.model_validate(data)


async def _infer_capabilities(rule: CapabilityRule) -> list[dict[str, Any]]:
    inferred: list[dict[str, Any]] = []
    for capability in rule.capabilities:
        await _ensure_capability(capability)
        result = await arcadedb_query(
            "gremlin",
            (
                "g.V().hasLabel('Device')"
                f".has('device_type', '{_escape_gremlin(rule.device_type.value)}')"
                ".as('device')"
                ".not(outE('HAS_CAPABILITY').inV()"
                f".has('name', '{_escape_gremlin(capability.value)}'))"
                ".select('device')"
                ".values('@rid')"
            ),
        )
        for device_rid in _result_values(result):
            if not isinstance(device_rid, str):
                continue
            await arcadedb_query(
                "sql",
                (
                    "CREATE EDGE HAS_CAPABILITY "
                    f"FROM {device_rid} "
                    f"TO (SELECT FROM Capability WHERE name = {_sql_value(capability)})"
                ),
                readonly=False,
            )
            inferred.append(
                {
                    "rule": "device_capability",
                    "device_type": rule.device_type.value,
                    "device": device_rid,
                    "capability": capability.value,
                }
            )
    return inferred


async def _infer_monitors(rule: MonitorRule) -> list[dict[str, Any]]:
    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('Device')"
            f".has('device_type', '{_escape_gremlin(rule.device_type.value)}')"
            ".as('device')"
            ".out('LOCATED_IN')"
            ".hasLabel('Room')"
            ".as('room')"
            ".select('device', 'room')"
            ".by(valueMap(true))"
        ),
    )

    inferred: list[dict[str, Any]] = []
    for pair in _result_dicts(result):
        device = _nested_dict(pair.get("device"))
        room = _nested_dict(pair.get("room"))
        device_name = _first_string(device.get("name"))
        room_rid = _rid(room)
        if not device_name or not room_rid:
            continue

        await arcadedb_query(
            "sql",
            (
                "UPDATE Sensor SET "
                f"name = {_sql_value(device_name)}, "
                f"sensor_type = {_sql_value(rule.sensor_type)}, "
                f"ha_entity_id = {_sql_value(_first_string(device.get('ha_entity_id')))}, "
                "created_at = datetime() "
                f"UPSERT WHERE name = {_sql_value(device_name)}"
            ),
            readonly=False,
        )
        sensor_selector = f"(SELECT FROM Sensor WHERE name = {_sql_value(device_name)})"
        await arcadedb_query(
            "sql",
            f"DELETE EDGE MONITORS FROM {sensor_selector} TO {room_rid}",
            readonly=False,
        )
        await arcadedb_query(
            "sql",
            (
                f"CREATE EDGE MONITORS FROM {sensor_selector} TO {room_rid} "
                f"SET confidence_score = {rule.confidence_score}"
            ),
            readonly=False,
        )
        inferred.append(
            {
                "rule": "sensor_monitors_room",
                "device_type": rule.device_type.value,
                "sensor": device_name,
                "room": room_rid,
                "confidence_score": rule.confidence_score,
            }
        )
    return inferred


async def _infer_can_perform(rule: AccessRule) -> list[dict[str, Any]]:
    await _ensure_action(rule.action)
    if rule.capability is not None:
        await _ensure_capability(rule.capability)
        await arcadedb_query(
            "sql",
            (
                "DELETE EDGE REQUIRES_CAPABILITY "
                f"FROM (SELECT FROM Action WHERE name = {_sql_value(rule.action)}) "
                f"TO (SELECT FROM Capability WHERE name = {_sql_value(rule.capability)})"
            ),
            readonly=False,
        )
        await arcadedb_query(
            "sql",
            (
                "CREATE EDGE REQUIRES_CAPABILITY "
                f"FROM (SELECT FROM Action WHERE name = {_sql_value(rule.action)}) "
                f"TO (SELECT FROM Capability WHERE name = {_sql_value(rule.capability)})"
            ),
            readonly=False,
        )

    result = await arcadedb_query(
        "gremlin",
        (
            "g.V().hasLabel('User')"
            f".has('role', '{_escape_gremlin(rule.role)}')"
            ".as('user')"
            ".out('HAS_ACCESS')"
            ".hasLabel('Room')"
            f".has('room_type', '{_escape_gremlin(rule.room_type.value)}')"
            ".as('room')"
            ".union(in('LOCATED_IN'), out('CONTAINS').hasLabel('Device'))"
            ".hasLabel('Device')"
            ".as('device')"
            ".select('user', 'device')"
            ".by(values('@rid'))"
        ),
    )

    inferred: list[dict[str, Any]] = []
    seen_users: set[str] = set()
    for pair in _result_dicts(result):
        user_rid = pair.get("user")
        device_rid = pair.get("device")
        if not isinstance(user_rid, str) or not isinstance(device_rid, str):
            continue
        if user_rid not in seen_users:
            await arcadedb_query(
                "sql",
                (
                    "DELETE EDGE CAN_PERFORM "
                    f"FROM {user_rid} "
                    f"TO (SELECT FROM Action WHERE name = {_sql_value(rule.action)})"
                ),
                readonly=False,
            )
            await arcadedb_query(
                "sql",
                (
                    "CREATE EDGE CAN_PERFORM "
                    f"FROM {user_rid} "
                    f"TO (SELECT FROM Action WHERE name = {_sql_value(rule.action)})"
                ),
                readonly=False,
            )
            seen_users.add(user_rid)
        inferred.append(
            {
                "rule": "user_can_perform_action",
                "role": rule.role,
                "room_type": rule.room_type.value,
                "user": user_rid,
                "action": rule.action.value,
                "device_context": device_rid,
            }
        )
    return inferred


async def _ensure_capability(capability: CapabilityName) -> None:
    await arcadedb_query(
        "sql",
        (
            "UPDATE Capability SET "
            f"name = {_sql_value(capability)}, "
            f"description = {_sql_value(f'{capability.value} capability')} "
            f"UPSERT WHERE name = {_sql_value(capability)}"
        ),
        readonly=False,
    )


async def _ensure_action(action: ActionName) -> None:
    await arcadedb_query(
        "sql",
        (
            "UPDATE Action SET "
            f"name = {_sql_value(action)}, "
            "parameters = {}, "
            "timestamp = datetime() "
            f"UPSERT WHERE name = {_sql_value(action)}"
        ),
        readonly=False,
    )


def _result_values(result: dict[str, Any]) -> list[Any]:
    rows = result.get("result", [])
    return rows if isinstance(rows, list) else [rows]


def _result_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _result_values(result) if isinstance(row, dict)]


def _nested_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rid(row: dict[str, Any]) -> str | None:
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


def _sql_value(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _escape_gremlin(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
