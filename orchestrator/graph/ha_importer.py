"""Import live Home Assistant state data into ArcadeDB."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from websockets.asyncio.client import connect as websocket_connect

from orchestrator.config import get_settings
from orchestrator.core.database import (
    arcadedb_query,
    ensure_arcadedb_database,
)
from orchestrator.graph.models import (
    DeviceType,
    HomeAssistantDomain,
    RoomType,
    SensorType,
    device_type_for_ha_domain,
)

logger = logging.getLogger(__name__)

SENSOR_DOMAINS = {"sensor", "binary_sensor"}
ENERGY_SENSOR_DEVICE_CLASSES = {
    "apparent_power",
    "battery",
    "current",
    "energy",
    "power",
    "power_factor",
    "voltage",
}
SECURITY_SENSOR_DEVICE_CLASSES = {
    "carbon_monoxide",
    "door",
    "gas",
    "lock",
    "moisture",
    "motion",
    "occupancy",
    "opening",
    "presence",
    "problem",
    "safety",
    "smoke",
    "tamper",
    "vibration",
    "window",
}
ENERGY_UNITS = {"w", "kw", "wh", "kwh", "v", "a", "va", "%"}
REASONING_DOMAINS = {
    "alarm_control_panel",
    "binary_sensor",
    "climate",
    "cover",
    "fan",
    "light",
    "lock",
    "sensor",
    "switch",
    "valve",
}
ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "arcade_schema.sql"
AREA_REGISTRY_PATH = ROOT / "ha_area_registry.json"
DEVICE_REGISTRY_PATH = ROOT / "ha_device_registry.json"
ENTITY_REGISTRY_PATH = ROOT / "ha_entity_registry.json"


class RegistryContext:
    """Local Home Assistant registry mappings exported from HA."""

    def __init__(
        self,
        areas: list[dict[str, Any]],
        devices: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        source: str = "local",
    ) -> None:
        self.source = source
        self.areas_by_id = {
            str(area.get("area_id")): area
            for area in areas
            if area.get("area_id")
        }
        self.devices_by_id = {
            str(device.get("id")): device
            for device in devices
            if device.get("id")
        }
        self.entities_by_id = {
            str(entity.get("entity_id")): entity
            for entity in entities
            if entity.get("entity_id")
        }

    @property
    def has_data(self) -> bool:
        return bool(self.areas_by_id or self.devices_by_id or self.entities_by_id)

    def room_for_entity(self, entity_id: str) -> dict[str, str]:
        entity = self.entities_by_id.get(entity_id, {})
        area_id = _optional_text(entity.get("area_id"))
        device = self.devices_by_id.get(str(entity.get("device_id") or ""), {})
        if area_id is None:
            area_id = _optional_text(device.get("area_id"))
        if area_id is None:
            return {"area_id": "home_assistant", "name": "Home Assistant"}
        area = self.areas_by_id.get(area_id, {})
        return {
            "area_id": area_id,
            "name": _optional_text(area.get("name")) or _title_from_id(area_id),
        }

    def device_for_entity(self, entity_id: str) -> dict[str, Any]:
        entity = self.entities_by_id.get(entity_id, {})
        device = self.devices_by_id.get(str(entity.get("device_id") or ""), {})
        return {"entity": entity, "device": device}


async def bootstrap_home_assistant_graph(limit: int | None = None) -> dict[str, Any]:
    """Create the graph database/schema and import current HA states.

    The import is idempotent. It creates one Home vertex, a logical
    "Home Assistant" room, Device vertices for every HA entity, Sensor vertices
    for sensor-like entities, and Observation vertices for current states.
    """
    settings = get_settings()
    created_database = await ensure_arcadedb_database(settings.ARCADEDB_DATABASE)
    schema_commands = await apply_arcadedb_schema(SCHEMA_PATH)
    states = await fetch_home_assistant_states()
    if limit is not None:
        states = states[:limit]
    registry = await fetch_registry_context()

    await _ensure_home(settings.HA_URL)
    await _ensure_home_assistant_room()
    rooms_imported = await _ensure_registry_rooms(registry)

    devices = 0
    sensors = 0
    observations = 0
    skipped_observations = 0
    edges = 0

    for state in states:
        entity_id = str(state.get("entity_id") or "")
        if not entity_id or "." not in entity_id:
            continue
        attributes = state.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        room = registry.room_for_entity(entity_id)
        registry_data = registry.device_for_entity(entity_id)

        await _upsert_device(
            entity_id=entity_id,
            attributes=attributes,
            room_area_id=room["area_id"],
            registry_data=registry_data,
        )
        devices += 1
        if room["area_id"] != "home_assistant":
            await _delete_edge(
                "LOCATED_IN",
                f"Device WHERE ha_entity_id = {_sql_string(entity_id)}",
                "Room WHERE ha_area_id = 'home_assistant'",
            )
        edges += await _replace_edge(
            "LOCATED_IN",
            f"Device WHERE ha_entity_id = {_sql_string(entity_id)}",
            f"Room WHERE ha_area_id = {_sql_string(room['area_id'])}",
        )

        if _domain(entity_id) in SENSOR_DOMAINS:
            await _upsert_sensor(entity_id, attributes)
            sensors += 1
            if room["area_id"] != "home_assistant":
                await _delete_edge(
                    "MONITORS",
                    f"Sensor WHERE ha_entity_id = {_sql_string(entity_id)}",
                    "Room WHERE ha_area_id = 'home_assistant'",
                )
            edges += await _replace_edge(
                "MONITORS",
                f"Sensor WHERE ha_entity_id = {_sql_string(entity_id)}",
                f"Room WHERE ha_area_id = {_sql_string(room['area_id'])}",
                "confidence_score = 1.0",
            )

        if await _should_upsert_observation(entity_id, state):
            await _upsert_observation(entity_id, state)
            observations += 1
            edges += await _replace_edge(
                "OBSERVED_IN",
                (
                    "Observation WHERE observation_type = 'ha_state' "
                    f"AND source_sensor = {_sql_string(entity_id)}"
                ),
                f"Device WHERE ha_entity_id = {_sql_string(entity_id)}",
            )
        else:
            skipped_observations += 1

    from orchestrator.graph.relationships import sync_graph_relationships

    relationship_result = await sync_graph_relationships()

    return {
        "created_database": created_database,
        "schema_commands": schema_commands,
        "home_assistant_states": len(states),
        "registry_loaded": registry.has_data,
        "registry_source": registry.source,
        "rooms": rooms_imported,
        "devices": devices,
        "sensors": sensors,
        "observations": observations,
        "skipped_observations": skipped_observations,
        "edges": edges,
        "relationships": relationship_result.model_dump(),
        "database": settings.ARCADEDB_DATABASE,
    }


async def apply_arcadedb_schema(schema_path: Path = SCHEMA_PATH) -> int:
    """Apply the ArcadeDB SQL schema file command by command."""
    commands = _split_sql_commands(schema_path.read_text(encoding="utf-8"))
    for command in commands:
        await arcadedb_query("sql", command, readonly=False)
    return len(commands)


async def fetch_home_assistant_states() -> list[dict[str, Any]]:
    """Fetch current Home Assistant states using the configured HA token."""
    settings = get_settings()
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import Home Assistant states.")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{settings.HA_URL}/api/states",
            headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Home Assistant /api/states did not return a list.")
    return [item for item in data if isinstance(item, dict)]


async def fetch_registry_context() -> RegistryContext:
    """Fetch HA registry metadata live, falling back to local exports if needed."""
    settings = get_settings()
    if settings.HA_REGISTRY_SOURCE.lower() == "local":
        return load_registry_context()

    try:
        registry = await fetch_home_assistant_registry()
    except Exception as exc:
        logger.warning("Falling back to local Home Assistant registry exports: %s", exc)
        return load_registry_context(source="local_fallback")
    if registry.has_data:
        return registry
    return load_registry_context(source="local_fallback")


async def fetch_home_assistant_registry() -> RegistryContext:
    """Fetch HA area/device/entity registries through the HA websocket API."""
    settings = get_settings()
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import Home Assistant registries.")

    websocket_url = _home_assistant_websocket_url(settings.HA_URL)
    async with websocket_connect(websocket_url) as websocket:
        auth_required = json.loads(await websocket.recv())
        if auth_required.get("type") != "auth_required":
            raise RuntimeError("Home Assistant websocket did not request auth.")

        await websocket.send(json.dumps({"type": "auth", "access_token": settings.HA_TOKEN}))
        auth_response = json.loads(await websocket.recv())
        if auth_response.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant websocket auth failed.")

        areas = await _ha_websocket_command(websocket, 1, "config/area_registry/list")
        devices = await _ha_websocket_command(websocket, 2, "config/device_registry/list")
        entities = await _ha_websocket_command(websocket, 3, "config/entity_registry/list")

    return RegistryContext(
        areas=_dict_list(areas),
        devices=_dict_list(devices),
        entities=_dict_list(entities),
        source="live",
    )


def load_registry_context(
    area_path: Path = AREA_REGISTRY_PATH,
    device_path: Path = DEVICE_REGISTRY_PATH,
    entity_path: Path = ENTITY_REGISTRY_PATH,
    source: str = "local",
) -> RegistryContext:
    """Load local HA registry exports when present."""
    return RegistryContext(
        areas=_read_registry_list(area_path),
        devices=_read_registry_list(device_path),
        entities=_read_registry_list(entity_path),
        source=source,
    )


async def _ensure_home(ha_url: str) -> None:
    await arcadedb_query(
        "sql",
        (
            "UPDATE Home SET "
            "name = 'EcoNest Home', "
            f"home_assistant_url = {_sql_string(ha_url)}, "
            f"created_at = {_sql_now()} "
            "UPSERT WHERE name = 'EcoNest Home'"
        ),
        readonly=False,
    )


async def _ensure_home_assistant_room() -> None:
    await arcadedb_query(
        "sql",
        (
            "UPDATE Room SET "
            "name = 'Home Assistant', "
            f"room_type = {_sql_string(RoomType.OTHER.value)}, "
            "description = 'Live entities imported from Home Assistant', "
            "ha_area_id = 'home_assistant', "
            f"created_at = {_sql_now()} "
            "UPSERT WHERE ha_area_id = 'home_assistant'"
        ),
        readonly=False,
    )
    await _replace_edge(
        "CONTAINS",
        "Home WHERE name = 'EcoNest Home'",
        "Room WHERE ha_area_id = 'home_assistant'",
    )


async def _ensure_registry_rooms(registry: RegistryContext) -> int:
    rooms = [
        {"area_id": area_id, "name": str(area.get("name") or _title_from_id(area_id))}
        for area_id, area in registry.areas_by_id.items()
    ]
    for room in rooms:
        room_type = _room_type(room["name"])
        await arcadedb_query(
            "sql",
            (
                "UPDATE Room SET "
                f"name = {_sql_string(room['name'])}, "
                f"room_type = {_sql_string(room_type)}, "
                f"description = {_sql_string('Home Assistant area import')}, "
                f"ha_area_id = {_sql_string(room['area_id'])}, "
                f"created_at = {_sql_now()} "
                f"UPSERT WHERE ha_area_id = {_sql_string(room['area_id'])}"
            ),
            readonly=False,
        )
        await _replace_edge(
            "CONTAINS",
            "Home WHERE name = 'EcoNest Home'",
            f"Room WHERE ha_area_id = {_sql_string(room['area_id'])}",
        )
    return len(rooms) + 1


async def _upsert_device(
    entity_id: str,
    attributes: dict[str, Any],
    room_area_id: str,
    registry_data: dict[str, Any],
) -> None:
    domain = _domain(entity_id)
    device_type = _device_type(domain, attributes)
    name = _device_name(entity_id, attributes, registry_data)
    device = registry_data.get("device", {})
    entity = registry_data.get("entity", {})
    fields = _assignments(
        {
            "name": name,
            "device_type": device_type,
            "ha_domain": domain,
            "ha_entity_id": entity_id,
            "ha_device_id": entity.get("device_id"),
            "ha_area_id": room_area_id,
            "ha_platform": entity.get("platform") or "home_assistant",
            "manufacturer": device.get("manufacturer"),
            "model": device.get("model"),
            "via_device_id": device.get("via_device_id"),
            "is_active": True,
            "created_at": _SqlRaw(_sql_now()),
        }
    )
    await arcadedb_query(
        "sql",
        (
            f"UPDATE Device SET {fields} "
            f"UPSERT WHERE ha_entity_id = {_sql_string(entity_id)}"
        ),
        readonly=False,
    )


async def _upsert_sensor(entity_id: str, attributes: dict[str, Any]) -> None:
    fields = _assignments(
        {
            "name": _friendly_name(entity_id, attributes),
            "sensor_type": _sensor_type(attributes),
            "unit": attributes.get("unit_of_measurement"),
            "ha_entity_id": entity_id,
            "device_class": attributes.get("device_class"),
            "state_class": attributes.get("state_class"),
            "created_at": _SqlRaw(_sql_now()),
        }
    )
    await arcadedb_query(
        "sql",
        (
            f"UPDATE Sensor SET {fields} "
            f"UPSERT WHERE ha_entity_id = {_sql_string(entity_id)}"
        ),
        readonly=False,
    )


async def _upsert_observation(entity_id: str, state: dict[str, Any]) -> None:
    attributes = state.get("attributes")
    context = _filter_empty_values(
        {
            "entity_id": entity_id,
            "attributes": attributes if isinstance(attributes, dict) else {},
            "last_changed": state.get("last_changed"),
            "last_updated": state.get("last_updated"),
            "last_reported": state.get("last_reported"),
        }
    )
    await arcadedb_query(
        "sql",
        (
            "UPDATE Observation SET "
            "observation_type = 'ha_state', "
            f"value = {_sql_string(str(state.get('state') or ''))}, "
            "confidence = 1.0, "
            f"source_sensor = {_sql_string(entity_id)}, "
            f"timestamp = {_sql_now()}, "
            f"context = {_sql_json(context)} "
            "UPSERT WHERE observation_type = 'ha_state' "
            f"AND source_sensor = {_sql_string(entity_id)}"
        ),
        readonly=False,
    )


async def _should_upsert_observation(entity_id: str, state: dict[str, Any]) -> bool:
    if _is_reasoning_relevant_state(entity_id, state):
        return True

    current_value = await _current_observation_value(entity_id)
    new_value = str(state.get("state") or "")
    return current_value != new_value


async def _current_observation_value(entity_id: str) -> str | None:
    result = await arcadedb_query(
        "sql",
        (
            "SELECT value FROM Observation "
            "WHERE observation_type = 'ha_state' "
            f"AND source_sensor = {_sql_string(entity_id)} "
            "LIMIT 1"
        ),
    )
    rows = result.get("result", [])
    if not rows or not isinstance(rows[0], dict):
        return None
    value = rows[0].get("value")
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value is None else str(value)


async def _replace_edge(
    edge_type: str,
    from_selector: str,
    to_selector: str,
    set_clause: str | None = None,
) -> int:
    from_query = f"(SELECT FROM {from_selector})"
    to_query = f"(SELECT FROM {to_selector})"
    command = f"CREATE EDGE {edge_type} FROM {from_query} TO {to_query}"
    command = f"{command} IF NOT EXISTS"
    if set_clause:
        command = f"{command} SET {set_clause}"
    await arcadedb_query("sql", command, readonly=False)
    return 1


async def _delete_edge(edge_type: str, from_selector: str, to_selector: str) -> None:
    await arcadedb_query(
        "sql",
        (
            f"DELETE FROM {edge_type} "
            f"WHERE @out IN (SELECT FROM {from_selector}) "
            f"AND @in IN (SELECT FROM {to_selector})"
        ),
        readonly=False,
    )


def _split_sql_commands(sql: str) -> list[str]:
    commands: list[str] = []
    current: list[str] = []
    for raw_line in sql.splitlines():
        line = raw_line.split("--", 1)[0].strip()
        if not line:
            continue
        current.append(line)
        if line.endswith(";"):
            commands.append(" ".join(current).rstrip(";").strip())
            current = []
    if current:
        commands.append(" ".join(current).strip())
    return commands


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _friendly_name(entity_id: str, attributes: dict[str, Any]) -> str:
    value = attributes.get("friendly_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return entity_id.split(".", 1)[-1].replace("_", " ").title()


def _device_name(
    entity_id: str,
    attributes: dict[str, Any],
    registry_data: dict[str, Any],
) -> str:
    device = registry_data.get("device", {})
    entity = registry_data.get("entity", {})
    for value in (
        device.get("name_by_user"),
        device.get("name"),
        entity.get("name"),
        entity.get("original_name"),
        attributes.get("friendly_name"),
    ):
        text = _optional_text(value)
        if text:
            return text
    return _friendly_name(entity_id, attributes)


def _device_type(domain: str, attributes: dict[str, Any]) -> str:
    if domain == "binary_sensor":
        device_class = str(attributes.get("device_class") or "").lower()
        if device_class in {"motion", "occupancy"}:
            return DeviceType.MOTION_SENSOR.value
    try:
        return device_type_for_ha_domain(HomeAssistantDomain(domain)).value
    except ValueError:
        return DeviceType.OTHER.value


def _is_reasoning_relevant_state(entity_id: str, state: dict[str, Any]) -> bool:
    domain = _domain(entity_id)
    attributes = state.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    device_class = str(attributes.get("device_class") or "").lower()
    unit = str(attributes.get("unit_of_measurement") or "").lower()

    if domain in {"alarm_control_panel", "cover", "lock", "valve"}:
        return True
    if domain in {"light", "switch", "fan", "climate"}:
        return True
    if domain == "binary_sensor":
        return device_class in SECURITY_SENSOR_DEVICE_CLASSES or not device_class
    if domain == "sensor":
        return (
            device_class in ENERGY_SENSOR_DEVICE_CLASSES
            or device_class in SECURITY_SENSOR_DEVICE_CLASSES
            or unit in ENERGY_UNITS
        )
    return domain in REASONING_DOMAINS


def _sensor_type(attributes: dict[str, Any]) -> str:
    device_class = str(attributes.get("device_class") or "").lower()
    if device_class in {item.value for item in SensorType}:
        return device_class
    unit = str(attributes.get("unit_of_measurement") or "").lower()
    if unit in {"w", "kw"}:
        return SensorType.POWER.value
    if unit in {"kwh", "wh"}:
        return SensorType.ENERGY.value
    if unit in {"v"}:
        return SensorType.VOLTAGE.value
    if unit in {"a"}:
        return SensorType.CURRENT.value
    return SensorType.OCCUPANCY.value if device_class == "occupancy" else SensorType.POWER.value


def _room_type(room_name: str) -> str:
    normalized = room_name.lower()
    if "bed" in normalized:
        return RoomType.BEDROOM.value
    if "kitchen" in normalized:
        return RoomType.KITCHEN.value
    if "garage" in normalized:
        return RoomType.GARAGE.value
    if "bath" in normalized:
        return RoomType.BATHROOM.value
    if "media" in normalized or "tv" in normalized:
        return RoomType.MEDIA_ROOM.value
    if "office" in normalized or "study" in normalized:
        return RoomType.OFFICE.value
    if "laundry" in normalized:
        return RoomType.LAUNDRY.value
    if "yard" in normalized or "outdoor" in normalized or "front" in normalized:
        return RoomType.OUTDOOR.value
    if "hvac" in normalized or "utility" in normalized:
        return RoomType.UTILITY.value
    if "living" in normalized:
        return RoomType.LIVING_ROOM.value
    return RoomType.OTHER.value


def _read_registry_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            for key in ("areas", "devices", "entities"):
                value = inner.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


async def _ha_websocket_command(
    websocket: Any,
    command_id: int,
    command_type: str,
) -> Any:
    await websocket.send(json.dumps({"id": command_id, "type": command_type}))
    while True:
        message = json.loads(await websocket.recv())
        if message.get("id") != command_id:
            continue
        if not message.get("success", False):
            raise RuntimeError(f"Home Assistant websocket command failed: {command_type}")
        return message.get("result")


def _home_assistant_websocket_url(ha_url: str) -> str:
    parsed = urlparse(ha_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            "/api/websocket",
            "",
            "",
            "",
        )
    )


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _title_from_id(value: str) -> str:
    return value.replace("_", " ").title()


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class _SqlRaw:
    def __init__(self, value: str) -> None:
        self.value = value


def _assignments(fields: dict[str, Any]) -> str:
    return ", ".join(
        f"{name} = {_sql_value(value)}"
        for name, value in fields.items()
        if _has_value(value)
    )


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _filter_empty_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if _has_value(value)}


def _sql_value(value: Any) -> str:
    if isinstance(value, _SqlRaw):
        return value.value
    if isinstance(value, bool):
        return str(value).lower()
    return _sql_string(str(value))


def _sql_now() -> str:
    return _sql_string(datetime.now(UTC).isoformat(sep=" "))


def _sql_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)
