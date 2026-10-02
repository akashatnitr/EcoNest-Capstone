"""Tests for conservative, advisory-only irrigation reasoning."""

from typing import Any

from orchestrator.agents.base import Task
from orchestrator.agents.irrigation_agent import IrrigationAgent
from orchestrator.mcp.models import ToolExecutionResult


class _IrrigationAgent(IrrigationAgent):
    def __init__(
        self, rows: list[dict[str, Any]], runs: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__()
        self.rows = rows
        self.runs = runs or []

    async def invoke_mcp_tool(
        self, task: Task, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        if "weather_forecasts" in arguments["sql"]:
            return ToolExecutionResult(
                success=True,
                capability="query_mysql",
                result=self.rows,
            )
        if "irrigation_runs" in arguments["sql"]:
            return ToolExecutionResult(
                success=True,
                capability="query_mysql",
                result=self.runs,
            )
        return ToolExecutionResult(
            success=True,
            capability="query_mysql",
            result=[{"ha_entity_id": "switch.back_lawn_automatic_watering"}],
        )


async def test_irrigation_agent_recommends_skipping_watering_for_rain() -> None:
    """Rain forecast evidence creates a recommendation without a device action."""
    agent = _IrrigationAgent(
        [{"condition_name": "rainy", "precipitation_in": 0.2}]
    )
    result = await agent.execute(
        Task(intent="Review irrigation", payload={"type": "irrigation"})
    )

    assert result.success is True
    assert result.data["recommendations"][0]["action"] == "Skip scheduled watering"
    assert "turn_on" not in str(result.data)


async def test_irrigation_agent_uses_gemma_to_explain_calculated_advice() -> None:
    """Gemma may explain weather-backed advice but cannot change valve control."""

    class _IrrigationLLM:
        async def generate(self, prompt: str, **_kwargs: Any) -> str:
            assert "EcoNest's irrigation recommendation explainer" in prompt
            return "Rain is forecast, so skipping scheduled watering is prudent."

    agent = _IrrigationAgent(
        [{"condition_name": "rainy", "precipitation_in": 0.2}]
    )
    agent.llm = _IrrigationLLM()
    result = await agent.execute(
        Task(
            intent="Give me a watering recommendation",
            payload={"type": "irrigation", "use_llm": True},
        )
    )

    recommendation = result.data["recommendations"][0]
    assert recommendation["action"] == "Skip scheduled watering"
    assert recommendation["reasoning"].startswith("A near-term forecast indicates rain")
    assert "No completed watering runs are recorded" in recommendation["reasoning"]
    assert result.data["model_explanation_used"] is True


async def test_irrigation_agent_includes_recent_watering_history() -> None:
    """Recommendations include retained runs and the first rainy forecast time."""
    agent = _IrrigationAgent(
        [
            {
                "forecast_at": "2026-10-01T20:00:00+00:00",
                "condition_name": "rainy",
                "precipitation_in": 0.2,
            }
        ],
        [
            {
                "zone_name": "Back Lawn",
                "started_at": "2026-09-30T12:00:00+00:00",
                "duration_seconds": 900,
            }
        ],
    )

    result = await agent.execute(
        Task(intent="Give me a watering recommendation", payload={"type": "irrigation"})
    )

    assert "earliest material rain: Oct 1 at 3 PM CT" in result.data["recommendations"][0]["reasoning"]
    assert "Back Lawn for 15 minutes" in result.data["watering_history_summary"]
    assert result.data["recent_irrigation_runs"][0]["zone_name"] == "Back Lawn"


async def test_irrigation_agent_answers_when_the_latest_run_completed() -> None:
    """A last-run question must search all retained runs, not only today's runs."""
    agent = _IrrigationAgent(
        [],
        [
            {
                "zone_name": "Vegetable Raised Beds",
                "started_at": "2026-09-30T14:00:00+00:00",
                "ended_at": "2026-09-30T14:25:00+00:00",
                "duration_seconds": 1500,
            }
        ],
    )

    result = await agent.execute(
        Task(
            intent="When did the irrigation system last run?",
            payload={"type": "irrigation_question"},
        )
    )

    assert result.success is True
    assert "Vegetable Raised Beds" in result.data["answer"]
    assert "Sep 30 at 9 AM CT" in result.data["answer"]
    assert "25 minutes" in result.data["answer"]


async def test_irrigation_agent_answers_recently_watered_question_with_latest_run() -> None:
    """Recent-watering wording is factual and must not return a forecast recommendation."""
    agent = _IrrigationAgent(
        [],
        [
            {
                "zone_name": "Front Lawn",
                "ended_at": "2026-10-01T10:00:00+00:00",
                "duration_seconds": 600,
            }
        ],
    )

    result = await agent.execute(
        Task(
            intent="Has the lawn been watered recently?",
            payload={"type": "irrigation_question"},
        )
    )

    assert result.success is True
    assert "Front Lawn" in result.data["answer"]
    assert "forecast" not in result.data["answer"].lower()


async def test_irrigation_agent_rejects_unsupported_model_claims() -> None:
    """The model cannot replace verified evidence with an invented soil claim."""

    class _UnsupportedIrrigationLLM:
        async def generate(self, _prompt: str, **_kwargs: Any) -> str:
            return "Recent watering suggests soil moisture levels are sufficient."

    agent = _IrrigationAgent(
        [{"condition_name": "rainy", "precipitation_in": 0.2}]
    )
    agent.llm = _UnsupportedIrrigationLLM()
    result = await agent.execute(
        Task(intent="Give me a watering recommendation", payload={"type": "irrigation", "use_llm": True})
    )

    assert "soil moisture" not in result.data["recommendations"][0]["reasoning"].lower()
    assert result.data["model_explanation_used"] is False
