"""Advisory-only irrigation recommendations backed by weather and device evidence."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from orchestrator.agents.base import BaseAgent, Result, Task


class IrrigationRecommendation(BaseModel):
    """One cautious watering recommendation for the household."""

    priority: str
    action: str
    reasoning: str


class IrrigationAgent(BaseAgent):
    """Assess weather and irrigation-zone context without controlling a valve."""

    name = "irrigation"
    tools = ["query_mysql", "ha_get_state"]
    permissions = ["device:read", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        """Handle explicit advisory irrigation reviews only."""
        return task.payload.get("type") == "irrigation" or any(
            word in task.intent.lower()
            for word in ("irrigation", "sprinkler", "watering")
        )

    async def run(self, task: Task) -> Result:
        """Build a recommendation from retained forecast and zone information."""
        forecasts = await self._forecast_rows(task)
        zones = await self._irrigation_zones(task)
        rainy = [row for row in forecasts if _forecast_indicates_rain(row)]

        if rainy:
            maximum_precipitation = max(
                (_number(row.get("precipitation_in")) or 0.0 for row in rainy),
                default=0.0,
            )
            recommendation = IrrigationRecommendation(
                priority="LOW",
                action="Skip scheduled watering",
                reasoning=(
                    "A near-term forecast indicates rain "
                    f"(up to {maximum_precipitation:.2f} in expected). "
                    "EcoNest recommends keeping irrigation off and reassessing after the forecast window."
                ),
            )
        elif forecasts:
            recommendation = IrrigationRecommendation(
                priority="LOW",
                action="Review watering schedule before running zones",
                reasoning=(
                    "No material rain is forecast in the next 24 hours. "
                    "EcoNest recommends reviewing the watering schedule before running zones."
                ),
            )
        else:
            recommendation = IrrigationRecommendation(
                priority="LOW",
                action="Keep irrigation under manual schedule",
                reasoning=(
                    "No current weather forecast is available to support a watering decision. "
                    "EcoNest will remain recommendation-only until forecast data is refreshed."
                ),
            )

        return Result(
            success=True,
            data={
                "mode": "recommendation_only",
                "recommendations": [recommendation.model_dump()],
                "forecast_observations": len(forecasts),
                "irrigation_zones": zones,
            },
            message="Irrigation review complete",
        )

    async def _forecast_rows(self, task: Task) -> list[dict[str, Any]]:
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT forecast_at, condition_name, precipitation_in, temperature_f "
                    "FROM weather_forecasts "
                    "WHERE forecast_at >= UTC_TIMESTAMP() "
                    "AND forecast_at < DATE_ADD(UTC_TIMESTAMP(), INTERVAL 24 HOUR) "
                    "ORDER BY forecast_at ASC LIMIT 24"
                )
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        return [row for row in rows if isinstance(row, dict)]

    async def _irrigation_zones(self, task: Task) -> list[str]:
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT ha_entity_id FROM devices "
                    "WHERE ha_entity_id LIKE 'valve.%' "
                    "OR ha_entity_id LIKE 'switch.%water%' "
                    "OR ha_entity_id LIKE 'switch.%sprinkler%' "
                    "OR ha_entity_id LIKE 'switch.%irrigation%' "
                    "ORDER BY ha_entity_id LIMIT 20"
                )
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        return [
            str(row["ha_entity_id"])
            for row in rows
            if isinstance(row, dict) and row.get("ha_entity_id")
        ]


def _forecast_indicates_rain(row: dict[str, Any]) -> bool:
    """Return whether a forecast row contains material rain evidence."""
    precipitation = _number(row.get("precipitation_in")) or 0.0
    condition = str(row.get("condition_name") or "").lower()
    return precipitation >= 0.05 or any(word in condition for word in ("rain", "storm", "shower"))


def _number(value: Any) -> float | None:
    """Parse an optional numeric forecast field."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
