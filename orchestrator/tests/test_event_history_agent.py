"""Tests for read-only recent household-event answers."""

from typing import Any

from orchestrator.agents.base import Task
from orchestrator.agents.event_history_agent import EventHistoryAgent
from orchestrator.mcp.models import ToolExecutionResult


class _EventHistoryAgent(EventHistoryAgent):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows

    async def invoke_mcp_tool(
        self, task: Task, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        assert name == "query_mysql"
        assert "home_events" in arguments["sql"] or "comfort_observations" in arguments["sql"]
        return ToolExecutionResult(
            success=True,
            capability="query_mysql",
            result=self.rows,
        )


async def test_event_history_agent_returns_retained_events() -> None:
    agent = _EventHistoryAgent(
        [{"event_type": "motion_detected", "entity_id": "binary_sensor.garage_motion"}]
    )

    result = await agent.execute(
        Task(intent="Tell me about recent important events", payload={"type": "event_history"})
    )

    assert result.success is True
    assert result.data["events"][0]["event_type"] == "motion_detected"
    assert result.data["answer"] == "EcoNest found 1 recent important household event."


async def test_event_history_agent_explains_when_no_events_are_retained() -> None:
    agent = _EventHistoryAgent([])

    result = await agent.execute(
        Task(intent="What happened recently?", payload={"type": "event_history"})
    )

    assert result.success is True
    assert result.data["events"] == []
    assert "No important household events" in result.data["answer"]


async def test_event_history_agent_answers_last_completed_appliance_cycle() -> None:
    agent = _EventHistoryAgent(
        [
            {
                "event_type": "appliance_cycle_completed",
                "entity_id": "sensor.dryer_power",
                "occurred_at": "2026-09-30 20:20:00",
                "device": "Dryer",
                "metadata": {"duration_seconds": 1140},
            }
        ]
    )

    result = await agent.execute(
        Task(
            intent="When was the last dryer cycle?",
            payload={"type": "event_history", "history_kind": "appliance_cycle", "event_search": "dryer"},
        )
    )

    assert result.success is True
    assert "last completed Dryer cycle ended at 2026-09-30 20:20:00" in result.data["answer"]
    assert "19 minutes" in result.data["answer"]


async def test_event_history_agent_calculates_gemma_planned_average_duration() -> None:
    agent = _EventHistoryAgent(
        [
            {"metadata": {"duration_seconds": 1_800}, "occurred_at": "2026-09-30 16:00:00"},
            {"metadata": {"duration_seconds": 2_400}, "occurred_at": "2026-09-29 16:00:00"},
        ]
    )

    result = await agent.execute(
        Task(
            intent="What is the average length of my dryer cycles?",
            payload={
                "type": "event_history",
                "history_analysis": {
                    "scope": "appliance_cycles",
                    "operation": "average_duration",
                    "subject": "dryer",
                    "period_days": 183,
                },
            },
        )
    )

    assert result.success is True
    assert "average duration" in result.data["answer"]
    assert "35 minutes" in result.data["answer"]
    assert result.data["plan"]["operation"] == "average_duration"


async def test_event_history_agent_answers_gemma_planned_room_comfort_query() -> None:
    agent = _EventHistoryAgent(
        [
            {
                "room": "Media Room",
                "target_temperature": 82,
                "current_temperature": 82,
                "humidity_percent": 54,
                "hvac_mode": "cool",
            }
        ]
    )

    result = await agent.execute(
        Task(
            intent="What is my comfort temperature in the media room?",
            payload={
                "type": "home_data",
                "room_comfort_query": {"room": "media room", "metric": "target_temperature"},
            },
        )
    )

    assert result.success is True
    assert "comfort target for Media Room is 82°F" in result.data["answer"]
