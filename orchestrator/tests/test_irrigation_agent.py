"""Tests for conservative, advisory-only irrigation reasoning."""

from typing import Any

from orchestrator.agents.base import Task
from orchestrator.agents.irrigation_agent import IrrigationAgent
from orchestrator.mcp.models import ToolExecutionResult


class _IrrigationAgent(IrrigationAgent):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows

    async def invoke_mcp_tool(
        self, task: Task, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        if "weather_forecasts" in arguments["sql"]:
            return ToolExecutionResult(
                success=True,
                capability="query_mysql",
                result=self.rows,
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
