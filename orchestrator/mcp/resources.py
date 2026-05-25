"""Semantic MCP resource providers."""

from typing import Any

from orchestrator.llm.memory import (
    get_memory_summaries,
    get_recent_interactions,
)


async def home_snapshot_resource() -> dict[str, Any]:
    return {
        "type": "snapshot",
        "rooms": [],
        "active_devices": [],
    }


async def home_devices_resource() -> dict[str, Any]:
    return {
        "type": "devices",
        "count": 0,
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
