"""Build bounded, explainable household context for agent reasoning."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.core.database import arcadedb_query

_GRAPH_LABELS = ("Room", "Device", "Sensor", "Observation")
ContextProvider = Callable[[], Awaitable[dict[str, Any]]]
RelevantGraphProvider = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]


class GraphBrainContextWrapper:
    """Standard wrapper for graph-backed, bounded household reasoning context."""

    name = "graph_brain"

    def __init__(
        self,
        snapshot_provider: ContextProvider,
        calendar_provider: ContextProvider,
        topology_provider: ContextProvider | None = None,
        relevant_graph_provider: RelevantGraphProvider | None = None,
        email_provider: ContextProvider | None = None,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self.calendar_provider = calendar_provider
        self.email_provider = email_provider
        self.topology_provider = topology_provider or graph_topology_summary
        self.relevant_graph_provider = relevant_graph_provider or relevant_graph_context

    async def status(self) -> dict[str, Any]:
        """Return graph wrapper availability without exposing graph records."""
        graph = await self.topology_provider()
        return {
            "name": self.name,
            "available": bool(graph.get("available")),
            "freshness": graph.get("freshness", "unknown"),
            "topology_counts": graph.get("topology_counts", {}),
            "warnings": graph.get("warnings", []),
            "execution_boundary": "Graph context is read-only evidence, not device authority.",
        }

    async def build_context(
        self, intent: str = "", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build one task-specific package from bounded source providers."""
        tasks = [
            self.snapshot_provider(),
            self.calendar_provider(),
            self.topology_provider(),
            self.relevant_graph_provider(intent, payload),
        ]
        if self.email_provider is not None:
            tasks.append(self.email_provider())
        results = await asyncio.gather(*tasks)
        snapshot, calendar, graph, relevant_graph = results[:4]
        email = results[4] if len(results) > 4 else None
        return build_household_context(snapshot, calendar, graph, relevant_graph, email)


def build_household_context(
    snapshot: dict[str, Any],
    calendar: dict[str, Any],
    graph: dict[str, Any],
    relevant_graph: dict[str, Any] | None = None,
    email: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine bounded sources without treating any non-HA source as live state."""
    return {
        "type": "household_context",
        "purpose": "Ground agent reasoning with bounded household evidence.",
        "execution_boundary": (
            "Context is evidence only. Device actions still require policy, "
            "capability, live-state, and confirmation checks."
        ),
        "live_snapshot": _bounded_snapshot(snapshot),
        "calendar_context": _bounded_calendar(calendar),
        "email_context": _bounded_email(email or {}),
        "graph_context": _bounded_graph(graph),
        "relevant_graph": _bounded_relevant_graph(relevant_graph or {}),
    }


async def graph_topology_summary() -> dict[str, Any]:
    """Return graph availability and topology counts without graph-record details."""
    counts: dict[str, int] = {}
    try:
        for label in _GRAPH_LABELS:
            result = await arcadedb_query("sql", f"SELECT count(*) AS count FROM {label}")
            counts[label.lower() + "s"] = _count_from_result(result)
    except Exception:
        return {
            "available": False,
            "freshness": "unknown",
            "warnings": ["ArcadeDB graph context is unavailable"],
        }
    return {
        "available": True,
        "freshness": "relationship context; not live device state",
        "topology_counts": counts,
    }


async def relevant_graph_context(
    intent: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve graph evidence relevant to any mentioned device or room."""
    payload = payload or {}
    try:
        device_rows = _graph_rows(
            await arcadedb_query(
                "sql",
                "SELECT name, ha_entity_id, device_type, ha_domain, ha_area_id FROM Device LIMIT 500",
            )
        )
        room_rows = _graph_rows(
            await arcadedb_query("sql", "SELECT name, ha_area_id FROM Room LIMIT 100")
        )
    except Exception:
        return {"available": False, "warnings": ["Relevant graph context is unavailable"]}

    mentioned_entities = _mentioned_entities(intent, payload)
    direct_devices = _match_devices(intent, mentioned_entities, device_rows)
    rooms = _match_rooms(intent, direct_devices, room_rows)
    room_area_ids = {room["area_id"] for room in rooms if room.get("area_id")}
    room_devices = [
        _device_view(row)
        for row in device_rows
        if str(row.get("ha_area_id") or "") in room_area_ids
    ]
    devices = _deduplicate([*direct_devices, *room_devices], "entity_id")[:4]
    rooms = _match_rooms(intent, devices, room_rows)
    details = await _relationship_details(devices, rooms)
    return {
        "available": True,
        "matched_devices": devices,
        "matched_rooms": rooms,
        **details,
        "freshness": "relationship context; verify all device state through Home Assistant",
    }


def _bounded_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("available"):
        return {
            "available": False,
            "warnings": list(snapshot.get("warnings") or [])[:3],
        }
    return {
        "available": True,
        "occupancy_status": str(snapshot.get("occupancy_status") or "unknown"),
        "lights_on": list(snapshot.get("lights_on") or [])[:8],
        "switches_on": list(snapshot.get("switches_on") or [])[:8],
        "covers_open": list(snapshot.get("covers_open") or [])[:8],
        "active_motion": list(snapshot.get("active_motion") or [])[:8],
        "inferred_room_activity": list(snapshot.get("inferred_room_activity") or [])[:8],
        "inferred_activity_timeout_minutes": snapshot.get("inferred_activity_timeout_minutes"),
        "top_power_now_w": list(snapshot.get("top_power_now_w") or [])[:8],
    }


def _bounded_calendar(calendar: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(calendar.get("available")),
        "current_mode": str(calendar.get("current_mode") or "normal"),
        "active_contexts": list(calendar.get("active_contexts") or [])[:3],
        "guidance": list(calendar.get("guidance") or [])[:4],
    }


def _bounded_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(graph.get("available")),
        "freshness": str(graph.get("freshness") or "unknown"),
        "topology_counts": dict(graph.get("topology_counts") or {}),
        "warnings": list(graph.get("warnings") or [])[:3],
    }


def _bounded_email(email: dict[str, Any]) -> dict[str, Any]:
    """Keep only derived notice categories in agent context."""
    return {
        "available": bool(email.get("available")),
        "notices": list(email.get("notices") or [])[:4],
        "guidance": list(email.get("guidance") or [])[:5],
    }


def _bounded_relevant_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(graph.get("available")),
        "matched_devices": list(graph.get("matched_devices") or [])[:4],
        "matched_rooms": list(graph.get("matched_rooms") or [])[:4],
        "related_sensors": list(graph.get("related_sensors") or [])[:8],
        "capabilities": list(graph.get("capabilities") or [])[:8],
        "warnings": list(graph.get("warnings") or [])[:3],
        "freshness": str(graph.get("freshness") or "unknown"),
    }


def _mentioned_entities(intent: str, payload: dict[str, Any]) -> set[str]:
    values = {
        str(payload[key])
        for key in ("entity_id", "device_entity_id")
        if isinstance(payload.get(key), str)
    }
    values.update(re.findall(r"\b[a-z_]+\.[a-z0-9_]+\b", intent.lower()))
    return values


def _match_devices(
    intent: str, mentioned_entities: set[str], rows: list[dict[str, Any]]
) -> list[dict[str, str]]:
    lowered = intent.lower().replace("_", " ")
    matches: list[dict[str, str]] = []
    for row in rows:
        entity_id = str(row.get("ha_entity_id") or "")
        name = str(row.get("name") or "")
        name_key = re.sub(r"\s+", " ", name.lower()).strip()
        entity_key = entity_id.replace("_", " ")
        if entity_id in mentioned_entities or (name_key and name_key in lowered) or (
            entity_key and entity_key in lowered
        ):
            matches.append(_device_view(row))
    return _deduplicate(matches, "entity_id")[:4]


def _match_rooms(
    intent: str, devices: list[dict[str, str]], rows: list[dict[str, Any]]
) -> list[dict[str, str]]:
    lowered = intent.lower()
    area_ids = {device.get("area_id") for device in devices if device.get("area_id")}
    matches = [
        _room_view(row)
        for row in rows
        if str(row.get("ha_area_id") or "") in area_ids
        or (str(row.get("name") or "").lower() in lowered and row.get("name"))
    ]
    return _deduplicate(matches, "area_id")[:4]


async def _relationship_details(
    devices: list[dict[str, str]], rooms: list[dict[str, str]]
) -> dict[str, list[dict[str, str]] | list[str]]:
    capabilities: list[str] = []
    sensors: list[dict[str, str]] = []
    try:
        for device in devices:
            entity_id = _gremlin_string(device["entity_id"])
            result = await arcadedb_query(
                "gremlin",
                "g.V().hasLabel('Device').has('ha_entity_id', '"
                + entity_id
                + "').out('HAS_CAPABILITY').values('name')",
            )
            capabilities.extend(_string_rows(result))
        for room in rooms:
            area_id = _gremlin_string(room["area_id"])
            result = await arcadedb_query(
                "gremlin",
                "g.V().hasLabel('Sensor').out('MONITORS').has('ha_area_id', '"
                + area_id
                + "').elementMap('name', 'sensor_type', 'ha_entity_id')",
            )
            sensors.extend(_sensor_view(row) for row in _graph_rows(result))
    except Exception:
        return {"capabilities": [], "related_sensors": []}
    return {
        "capabilities": sorted(set(capabilities))[:8],
        "related_sensors": _deduplicate(sensors, "entity_id")[:8],
    }


def _graph_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("result")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _string_rows(result: dict[str, Any]) -> list[str]:
    rows = result.get("result")
    if not isinstance(rows, list):
        return []
    return [str(row) for row in rows if isinstance(row, (str, int, float))]


def _device_view(row: dict[str, Any]) -> dict[str, str]:
    return {
        "entity_id": str(row.get("ha_entity_id") or ""),
        "name": str(row.get("name") or ""),
        "type": str(row.get("device_type") or ""),
        "domain": str(row.get("ha_domain") or ""),
        "area_id": str(row.get("ha_area_id") or ""),
    }


def _room_view(row: dict[str, Any]) -> dict[str, str]:
    return {"name": str(row.get("name") or ""), "area_id": str(row.get("ha_area_id") or "")}


def _sensor_view(row: dict[str, Any]) -> dict[str, str]:
    return {
        "entity_id": str(row.get("ha_entity_id") or ""),
        "name": str(row.get("name") or ""),
        "type": str(row.get("sensor_type") or ""),
    }


def _deduplicate(rows: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    return [row for row in rows if (value := row.get(key)) and not (value in seen or seen.add(value))]


def _gremlin_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _count_from_result(result: dict[str, Any]) -> int:
    rows = result.get("result")
    if not isinstance(rows, list) or not rows:
        return 0
    row = rows[0]
    if not isinstance(row, dict):
        return 0
    try:
        return int(row.get("count"))
    except (TypeError, ValueError):
        return 0
