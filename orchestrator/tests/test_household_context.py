"""Tests for bounded graph-backed household context."""

from orchestrator.core.household_context import (
    GraphBrainContextWrapper,
    _match_devices,
    _match_rooms,
    build_household_context,
)
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.agents.base import Task


class _ContextExecutor:
    """Minimal MCP executor used to verify orchestration context attachment."""

    async def read_resource(self, uri: str, **_: object) -> dict[str, object]:
        assert uri == "home://household-context"
        return {"type": "household_context", "graph_context": {"available": True}}


def test_household_context_keeps_graph_as_non_authoritative_evidence() -> None:
    """Live HA facts, graph topology, and Calendar context have distinct roles."""
    result = build_household_context(
        {
            "available": True,
            "occupancy_status": "away",
            "lights_on": [{"entity_id": "light.media", "state": "on"}],
            "switches_on": [],
            "covers_open": [],
            "active_motion": [],
            "top_power_now_w": [{"entity_id": "sensor.dryer", "value": 900.0}],
        },
        {
            "available": True,
            "current_mode": "away",
            "active_contexts": [{"mode": "away"}],
            "guidance": ["Favor energy-saving recommendations"],
        },
        {
            "available": True,
            "freshness": "relationship context; not live device state",
            "topology_counts": {"rooms": 3, "devices": 10},
        },
    )

    assert result["type"] == "household_context"
    assert result["live_snapshot"]["occupancy_status"] == "away"
    assert result["calendar_context"]["current_mode"] == "away"
    assert result["graph_context"]["topology_counts"]["devices"] == 10
    assert "evidence only" in result["execution_boundary"]


def test_household_context_bounds_unavailable_sources() -> None:
    """Unavailable sources remain visible without leaking unbounded payloads."""
    result = build_household_context(
        {"available": False, "warnings": ["HA unavailable", "ignored", "ignored", "ignored"]},
        {"available": False},
        {"available": False, "warnings": ["Graph unavailable"]},
    )

    assert result["live_snapshot"] == {
        "available": False,
        "warnings": ["HA unavailable", "ignored", "ignored"],
    }
    assert result["calendar_context"]["current_mode"] == "normal"
    assert result["graph_context"]["available"] is False


async def test_orchestrator_attaches_context_only_for_llm_reasoning() -> None:
    """The bounded package is added before Gemma reasoning, not every task."""
    orchestrator = AgentOrchestrator(tool_executor=_ContextExecutor())

    llm_task = await orchestrator._attach_household_context(
        Task(intent="review energy", payload={"use_llm": True})
    )
    deterministic_task = await orchestrator._attach_household_context(
        Task(intent="review energy", payload={})
    )

    assert llm_task.metadata["household_context"]["type"] == "household_context"
    assert "household_context" not in deterministic_task.metadata


def test_graph_matching_resolves_entity_ids_and_room_names() -> None:
    """Any current graph device/room can be selected from a natural-language task."""
    devices = _match_devices(
        "Turn off light.upstairs_media_light_1 in the media room",
        {"light.upstairs_media_light_1"},
        [
            {
                "name": "Lights Light 1",
                "ha_entity_id": "light.upstairs_media_light_1",
                "device_type": "SmartBulb",
                "ha_domain": "light",
                "ha_area_id": "media_room",
            }
        ],
    )
    rooms = _match_rooms(
        "Turn off the media room light", devices, [{"name": "Media Room", "ha_area_id": "media_room"}]
    )

    assert devices[0]["entity_id"] == "light.upstairs_media_light_1"
    assert rooms == [{"name": "Media Room", "area_id": "media_room"}]


async def test_graph_brain_wrapper_has_standard_status_and_context_methods() -> None:
    """The graph implementation follows the reusable context-wrapper contract."""

    async def snapshot() -> dict[str, object]:
        return {"available": True, "occupancy_status": "home"}

    async def calendar() -> dict[str, object]:
        return {"available": True, "current_mode": "normal"}

    async def topology() -> dict[str, object]:
        return {"available": True, "freshness": "relationship context", "topology_counts": {"rooms": 2}}

    async def relevant(_: str, __: dict[str, object] | None) -> dict[str, object]:
        return {"available": True, "matched_devices": [{"entity_id": "light.one"}]}

    wrapper = GraphBrainContextWrapper(snapshot, calendar, topology, relevant)
    status = await wrapper.status()
    context = await wrapper.build_context("turn off light.one", {"use_llm": True})

    assert status["name"] == "graph_brain"
    assert status["available"] is True
    assert context["relevant_graph"]["matched_devices"] == [{"entity_id": "light.one"}]
