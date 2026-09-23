"""Semantic MCP resource providers."""

from typing import Any

import httpx

from orchestrator.config import get_settings

from orchestrator.llm.memory import (
    get_memory_summaries,
    get_recent_interactions,
)

settings = get_settings()

_CONTROLLABLE_ACTIONS: dict[str, list[str]] = {
    "light": ["turn_on", "turn_off", "set_brightness"],
    "switch": ["turn_on", "turn_off"],
    "fan": ["turn_on", "turn_off"],
    "cover": ["open", "close", "turn_on", "turn_off"],
    "climate": ["turn_on", "turn_off", "set_temperature"],
}


async def home_snapshot_resource() -> dict[str, Any]:
    return {
        "type": "snapshot",
        "rooms": [],
        "active_devices": [],
    }


async def home_devices_resource() -> dict[str, Any]:
    """Return a compact, live inventory of controllable HA entities."""
    if not settings.HA_TOKEN:
        return {
            "type": "devices",
            "count": 0,
            "devices": [],
            "warnings": ["Home Assistant is not configured"],
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.HA_URL.rstrip('/')}/api/states",
                headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
            )
            response.raise_for_status()
            states = response.json()
    except httpx.HTTPError:
        return {
            "type": "devices",
            "count": 0,
            "devices": [],
            "warnings": ["Home Assistant inventory is unavailable"],
        }
    if not isinstance(states, list):
        raise RuntimeError("Home Assistant /api/states did not return a list")

    devices: list[dict[str, Any]] = []
    for state in states:
        if not isinstance(state, dict):
            continue
        entity_id = str(state.get("entity_id") or "")
        domain, separator, _ = entity_id.partition(".")
        if not separator or domain not in _CONTROLLABLE_ACTIONS:
            continue
        attributes = state.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        devices.append(
            {
                "entity_id": entity_id,
                "name": str(attributes.get("friendly_name") or entity_id),
                "domain": domain,
                "state": str(state.get("state") or "unknown"),
                "actions": _CONTROLLABLE_ACTIONS[domain],
            }
        )
    return {
        "type": "devices",
        "count": len(devices),
        "devices": devices,
    }


async def home_analytics_resource() -> dict[str, Any]:
    return {
        "type": "analytics",
        "hourly_power": [],
    }


async def ontology_resource() -> dict[str, Any]:
    return {
        "type": "ontology",
        "classes": [],
    }


async def recent_memory_resource(
    user_id: str,
) -> dict[str, Any]:
    summaries = await get_memory_summaries(user_id)
    interactions = await get_recent_interactions(user_id)

    return {
        "type": "memory",
        "recent_summaries": summaries,
        "recent_interactions": interactions,
    }
