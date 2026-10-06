"""Advisory-only irrigation recommendations backed by weather and device evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from orchestrator.agents.base import BaseAgent, Result, Task

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "irrigation.j2"


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
        """Handle explicit irrigation reviews and retained-activity questions."""
        return task.payload.get("type") in {
            "irrigation",
            "irrigation_question",
        } or any(
            word in task.intent.lower()
            for word in ("irrigation", "sprinkler", "watering")
        )

    async def _live_entity_state(
        self,
        task: Task,
    ) -> dict[str, Any]:
        """Read the triggering Home Assistant entity through MCP."""
        entity_id = str(task.payload.get("entity_id") or "").strip()
        if not entity_id:
            return {
                "available": False,
                "warnings": ["No triggering Home Assistant entity was provided"],
            }

        try:
            result = await self.invoke_mcp_tool(
                task,
                "ha_get_state",
                {"entity_id": entity_id},
            )

            if result.success:
                return result.result

            return {
                "available": False,
                "warnings": result.warnings,
            }
        except Exception:
            return {
                "available": False,
                "warnings": ["Live Home Assistant state unavailable"],
            }

    async def run(self, task: Task) -> Result:
        """Build a recommendation from retained forecast and zone information."""
        if task.payload.get("type") == "irrigation_question":
            return await self._answer_irrigation_activity_question(task)
        forecasts = await self._forecast_rows(task)
        zones = await self._irrigation_zones(task)
        recent_runs = await self._recent_irrigation_runs(task)
        live_entity_state = await self._live_entity_state(task)
        watering_history = _watering_history_summary(recent_runs)
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
                    f"(up to {maximum_precipitation:.2f} in expected; "
                    f"earliest material rain: {_forecast_time_label(rainy[0])}). "
                    f"{watering_history} "
                    "EcoNest recommends keeping irrigation off and reassessing after the forecast window."
                ),
            )
        elif forecasts:
            recommendation = IrrigationRecommendation(
                priority="LOW",
                action="Review watering schedule before running zones",
                reasoning=(
                    "No material rain is forecast in the next 24 hours. "
                    f"{watering_history} "
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

        llm_reasoning = await self._llm_reasoning(
            task,
            recommendation,
            forecasts,
            zones,
            recent_runs,
            watering_history,
            live_entity_state,
        )
        model_explanation_used = False
        if llm_reasoning and _is_safe_llm_explanation(llm_reasoning):
            recommendation = recommendation.model_copy(
                update={
                    "reasoning": (
                        f"{recommendation.reasoning} {llm_reasoning}"
                    )
                }
            )
            model_explanation_used = True

        return Result(
            success=True,
            data={
                "mode": "recommendation_only",
                "recommendations": [recommendation.model_dump()],
                "forecast_observations": len(forecasts),
                "irrigation_zones": zones,
                "recent_irrigation_runs": recent_runs,
                "watering_history_summary": watering_history,
                "model_explanation_used": model_explanation_used,
            },
            message="Irrigation review complete",
        )

    async def _llm_reasoning(
        self,
        task: Task,
        recommendation: IrrigationRecommendation,
        forecasts: list[dict[str, Any]],
        zones: list[str],
        recent_runs: list[dict[str, Any]],
        watering_history: str,
        live_entity_state: dict[str, Any],
    ) -> str | None:
        """Ask Gemma to explain, but not alter, calculated watering advice."""
        if task.payload.get("use_llm") is not True or not PROMPT_PATH.exists():
            return None
        prompt = _render_prompt(
            PROMPT_PATH.read_text(encoding="utf-8"),
            {
                "intent": task.intent,
                "forecast": forecasts,
                "zones": zones,
                "recent_runs": recent_runs,
                "watering_history": watering_history,
                "recommendation": recommendation.model_dump(),
                "live_entity_state": live_entity_state,
            },
        )
        prompt += await self.reviewed_feedback_guidance(task)
        try:
            response = await self.llm.generate(prompt, temperature=0.2)
        except Exception:
            return None
        cleaned = response.strip()
        return cleaned[:600] if cleaned else None

    async def _answer_irrigation_activity_question(self, task: Task) -> Result:
        """Answer a factual retained-irrigation question without controlling a valve."""
        intent = task.intent.lower()
        asks_for_latest = any(
            phrase in intent
            for phrase in (
                "when did",
                "last run",
                "last ran",
                "most recent",
                "latest",
                "recently",
                "been watered",
            )
        )
        if asks_for_latest:
            return await self._answer_latest_irrigation_run(task)
        central = ZoneInfo("America/Chicago")
        now = datetime.now(central)
        start_local = datetime.combine(now.date(), time.min, tzinfo=central)
        end_local = datetime.combine(now.date(), time.max, tzinfo=central)
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT zone_name, started_at, ended_at, duration_seconds "
                    "FROM irrigation_runs "
                    "WHERE started_at >= :start_at AND started_at <= :end_at "
                    "ORDER BY started_at ASC LIMIT 50"
                ),
                "params": {
                    "start_at": start_local.astimezone(UTC).replace(tzinfo=None),
                    "end_at": end_local.astimezone(UTC).replace(tzinfo=None),
                },
            },
        )
        runs = result.result if result.success and isinstance(result.result, list) else []
        if not runs:
            answer = "No completed irrigation runs are recorded for today in Central time."
        else:
            descriptions = []
            for run in runs:
                zone = str(run.get("zone_name") or "Unknown zone")
                duration = int(run.get("duration_seconds") or 0) // 60
                descriptions.append(f"{zone} ({duration} minutes)")
            answer = (
                f"Yes. EcoNest found {len(runs)} completed irrigation run"
                f"{'s' if len(runs) != 1 else ''} today in Central time: "
                f"{'; '.join(descriptions)}."
            )
        return Result(
            success=result.success,
            data={"answer": answer, "runs": runs},
            message="Irrigation activity answer complete",
        )

    async def _answer_latest_irrigation_run(self, task: Task) -> Result:
        """Return the latest completed run across retained irrigation history."""
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT zone_name, started_at, ended_at, duration_seconds "
                    "FROM irrigation_runs WHERE ended_at IS NOT NULL "
                    "ORDER BY ended_at DESC LIMIT 1"
                )
            },
        )
        runs = result.result if result.success and isinstance(result.result, list) else []
        if not runs:
            answer = "No completed irrigation runs have been retained yet."
        else:
            run = runs[0]
            zone = str(run.get("zone_name") or "Unknown zone")
            ended_at = _forecast_time_label({"forecast_at": run.get("ended_at")})
            duration = int(_number(run.get("duration_seconds")) or 0) // 60
            answer = (
                f"The most recent completed irrigation run was {zone}, ending {ended_at} "
                f"after {duration} minutes."
            )
        return Result(
            success=result.success,
            data={"answer": answer, "runs": runs},
            message="Irrigation activity answer complete",
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

    async def _recent_irrigation_runs(self, task: Task) -> list[dict[str, Any]]:
        """Retrieve a bounded recent watering history for the recommendation."""
        result = await self.invoke_mcp_tool(
            task,
            "query_mysql",
            {
                "sql": (
                    "SELECT zone_name, started_at, ended_at, duration_seconds "
                    "FROM irrigation_runs "
                    "WHERE started_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 14 DAY) "
                    "ORDER BY started_at DESC LIMIT 20"
                )
            },
        )
        rows = result.result if result.success and isinstance(result.result, list) else []
        return [row for row in rows if isinstance(row, dict)]


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


def _forecast_time_label(row: dict[str, Any]) -> str:
    """Return the forecast time in the household's local time zone when available."""
    value = row.get("forecast_at")
    if not value:
        return "time unavailable"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "time unavailable"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(ZoneInfo("America/Chicago")).strftime("%b %-d at %-I %p CT")


def _watering_history_summary(runs: list[dict[str, Any]]) -> str:
    """Describe retained recent watering evidence without inferring an ideal schedule."""
    if not runs:
        return "No completed watering runs are recorded in the last 14 days."
    latest = runs[0]
    zone = str(latest.get("zone_name") or "an unknown zone")
    duration = int(_number(latest.get("duration_seconds")) or 0) // 60
    started_at = _forecast_time_label({"forecast_at": latest.get("started_at")})
    return (
        f"The most recent recorded watering was {zone} for {duration} minutes "
        f"on {started_at}; {len(runs)} completed run(s) are recorded in the last 14 days."
    )


def _is_safe_llm_explanation(explanation: str) -> bool:
    """Reject model wording that introduces evidence EcoNest did not retrieve."""
    lowered = explanation.lower()
    unsupported_phrases = (
        "soil moisture",
        "soil-moisture",
        "soil is",
        "moisture levels",
    )
    return not any(phrase in lowered for phrase in unsupported_phrases)


def _render_prompt(template: str, values: dict[str, Any]) -> str:
    """Render the small JSON-backed irrigation explanation template."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", json.dumps(value, default=str))
        rendered = rendered.replace("{{" + key + "}}", json.dumps(value, default=str))
    return rendered
