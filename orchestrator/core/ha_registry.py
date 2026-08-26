"""Shared Home Assistant registry loading and area resolution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from websockets.asyncio.client import connect as websocket_connect

from orchestrator.config import get_settings

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
AREA_REGISTRY_PATH = ROOT / "ha_area_registry.json"
DEVICE_REGISTRY_PATH = ROOT / "ha_device_registry.json"
ENTITY_REGISTRY_PATH = ROOT / "ha_entity_registry.json"


class RegistryContext:
    """Home Assistant area, device, and entity registry mappings."""

    def __init__(
        self,
        areas: list[dict[str, Any]],
        devices: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        source: str = "local",
    ) -> None:
        self.source = source
        self.areas_by_id = {
            str(area["area_id"]): area for area in areas if area.get("area_id")
        }
        self.devices_by_id = {
            str(device["id"]): device for device in devices if device.get("id")
        }
        self.entities_by_id = {
            str(entity["entity_id"]): entity
            for entity in entities
            if entity.get("entity_id")
        }

    @property
    def has_data(self) -> bool:
        """Whether any HA registry records were loaded."""
        return bool(self.areas_by_id or self.devices_by_id or self.entities_by_id)

    def room_for_entity(self, entity_id: str) -> dict[str, str]:
        """Resolve direct entity area, then parent-device area, then fallback."""
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
        """Return the source registry entity and its parent device, if any."""
        entity = self.entities_by_id.get(entity_id, {})
        device = self.devices_by_id.get(str(entity.get("device_id") or ""), {})
        return {"entity": entity, "device": device}


async def fetch_registry_context() -> RegistryContext:
    """Fetch the live HA registry, falling back to local exports if available."""
    settings = get_settings()
    if settings.HA_REGISTRY_SOURCE.lower() == "local":
        return load_registry_context()
    try:
        registry = await fetch_home_assistant_registry()
    except Exception as exc:
        logger.warning("Falling back to local Home Assistant registry exports: %s", exc)
        return load_registry_context(source="local_fallback")
    return registry if registry.has_data else load_registry_context(source="local_fallback")


async def fetch_home_assistant_registry() -> RegistryContext:
    """Fetch HA registries through its authenticated websocket API."""
    settings = get_settings()
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import Home Assistant registries.")
    async with websocket_connect(_home_assistant_websocket_url(settings.HA_URL)) as ws:
        required = json.loads(await ws.recv())
        if required.get("type") != "auth_required":
            raise RuntimeError("Home Assistant websocket did not request auth.")
        await ws.send(json.dumps({"type": "auth", "access_token": settings.HA_TOKEN}))
        if json.loads(await ws.recv()).get("type") != "auth_ok":
            raise RuntimeError("Home Assistant websocket auth failed.")
        areas = await _ha_websocket_command(ws, 1, "config/area_registry/list")
        devices = await _ha_websocket_command(ws, 2, "config/device_registry/list")
        entities = await _ha_websocket_command(ws, 3, "config/entity_registry/list")
    return RegistryContext(_dict_list(areas), _dict_list(devices), _dict_list(entities), "live")


def load_registry_context(
    area_path: Path = AREA_REGISTRY_PATH,
    device_path: Path = DEVICE_REGISTRY_PATH,
    entity_path: Path = ENTITY_REGISTRY_PATH,
    source: str = "local",
) -> RegistryContext:
    """Load locally exported HA registry files."""
    return RegistryContext(
        _read_registry_list(area_path), _read_registry_list(device_path),
        _read_registry_list(entity_path), source,
    )


async def _ha_websocket_command(websocket: Any, command_id: int, command_type: str) -> Any:
    await websocket.send(json.dumps({"id": command_id, "type": command_type}))
    while True:
        message = json.loads(await websocket.recv())
        if message.get("id") != command_id:
            continue
        if not message.get("success", False):
            raise RuntimeError(f"Home Assistant websocket command failed: {command_type}")
        return message.get("result")


def _read_registry_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return _dict_list(data)
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, dict):
            for key in ("areas", "devices", "entities"):
                if isinstance(inner.get(key), list):
                    return _dict_list(inner[key])
    return []


def _home_assistant_websocket_url(ha_url: str) -> str:
    parsed = urlparse(ha_url)
    return urlunparse(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/api/websocket", "", "", ""))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _title_from_id(value: str) -> str:
    return value.replace("_", " ").title()
