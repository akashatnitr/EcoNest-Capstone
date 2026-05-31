"""Energy optimization agent."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.config import get_settings
from orchestrator.core.database import arcadedb_query, mysql_session_context

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "energy.j2"

PEAK_START_HOUR = 16
PEAK_END_HOUR = 21
OFF_PEAK_START_HOUR = 21
ANOMALY_MULTIPLIER_THRESHOLD = 4.0
MEANINGFUL_POWER_WATTS = 100.0


class PricingSnapshot(BaseModel):
    """Time-of-use pricing information used by the energy agent."""

    current_tier: str
    cents_per_kwh: float
    peak_hours: str = "4pm-9pm"
    off_peak_hours: str = "after 9pm"
    next_cheap_window: str = "after 9pm tonight"


class EnergyObservation(BaseModel):
    """Current or recent energy signal."""

    entity_id: str | None = None
    name: str = "Unknown"
    room: str | None = None
    current_power_w: float = 0.0
    baseline_w: float | None = None
    scheduled: bool | None = None
    schedule: str | None = None
    anomaly_reason: str | None = None


class EnergyRecommendation(BaseModel):
    """Structured recommendation produced by the energy agent."""

    priority: str = Field(pattern="^(LOW|MEDIUM|HIGH)$")
    action: str
    reasoning: str
    estimated_savings_kwh: float | None = None


class EnergyAgentOutput(BaseModel):
    """Energy agent response payload."""

    mode: str
    pricing: PricingSnapshot
    recommendations: list[EnergyRecommendation] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    anomalies: list[EnergyObservation] = Field(default_factory=list)
    schedule_violations: list[EnergyObservation] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class EnergyAgent(BaseAgent):
    """Responsibilities: power optimization, TOU pricing, schedules, anomalies."""

    name = "energy"
    tools = ["query_mysql", "query_arcadedb", "ha_get_state", "ha_turn_off"]
    permissions = ["device:read", "device:write", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = [
            "energy",
            "power",
            "efficiency",
            "pricing",
            "schedule",
            "cheap",
            "tou",
            "peak",
            "kwh",
            "anomaly",
        ]
        return any(kw in task.intent.lower() for kw in keywords) or (
            task.payload.get("type") == "energy"
        )

    async def run(self, task: Task) -> Result:
        context = await self._build_context(task)
        pricing = self._pricing_for_hour(context["current_hour"])
        observations = self._observations_from_payload(task.payload)
        observations.extend(await self._observations_from_graph())

        anomalies = self._detect_anomalies(observations)
        schedule_violations = self._detect_schedule_violations(observations)
        recommendations = self._build_recommendations(
            pricing,
            observations,
            anomalies,
            schedule_violations,
        )
        alerts = self._build_alerts(pricing, anomalies, schedule_violations)

        llm_recommendation = await self._llm_recommendation(
            task,
            pricing,
            anomalies,
            schedule_violations,
            recommendations,
        )
        if llm_recommendation:
            recommendations.insert(0, llm_recommendation)

        output = EnergyAgentOutput(
            mode=self._mode(task),
            pricing=pricing,
            recommendations=recommendations,
            alerts=alerts,
            anomalies=anomalies,
            schedule_violations=schedule_violations,
            context=context,
        )
        return Result(
            success=True,
            data={
                **output.model_dump(),
                "recommendation": (
                    recommendations[0].action
                    if recommendations
                    else "No energy action needed right now"
                ),
            },
            message="Energy review complete",
        )

    async def _build_context(self, task: Task) -> dict[str, Any]:
        now = datetime.now()
        context = {
            "current_hour": int(task.payload.get("current_hour", now.hour)),
            "source": task.metadata.get("source", "direct"),
            "trigger": task.metadata.get("event_type")
            or task.metadata.get("schedule_id")
            or task.payload.get("trigger")
            or "manual",
        }
        context["mysql"] = await self._mysql_energy_context()
        return context

    def _pricing_for_hour(self, hour: int) -> PricingSnapshot:
        if PEAK_START_HOUR <= hour < PEAK_END_HOUR:
            return PricingSnapshot(
                current_tier="peak",
                cents_per_kwh=18.0,
                next_cheap_window="after 9pm tonight",
            )
        return PricingSnapshot(
            current_tier="off_peak",
            cents_per_kwh=9.0,
            next_cheap_window="now",
        )

    def _observations_from_payload(
        self, payload: dict[str, Any]
    ) -> list[EnergyObservation]:
        observations: list[EnergyObservation] = []
        for raw in payload.get("observations", []):
            if isinstance(raw, dict):
                observations.append(self._observation_from_mapping(raw))

        current_power = payload.get("current_power_w") or payload.get("power_trend")
        if current_power is not None:
            observations.append(
                EnergyObservation(
                    entity_id=_optional_str(payload.get("entity_id")),
                    name=str(
                        payload.get("device_name")
                        or payload.get("room")
                        or "Energy signal"
                    ),
                    room=_optional_str(payload.get("room")),
                    current_power_w=_float(current_power),
                    baseline_w=_optional_float(payload.get("baseline_w")),
                    scheduled=_optional_bool(payload.get("scheduled")),
                    schedule=_optional_str(payload.get("schedule")),
                    anomaly_reason=_optional_str(payload.get("reason")),
                )
            )
        return observations

    async def _observations_from_graph(self) -> list[EnergyObservation]:
        try:
            result = await arcadedb_query(
                "sql",
                (
                    "SELECT source_sensor AS entity_id, value, name "
                    "FROM Observation "
                    "WHERE observation_type = 'ha_state' "
                    "AND source_sensor LIKE 'sensor.%energy%' "
                    "LIMIT 10"
                ),
            )
        except Exception:
            return []

        observations: list[EnergyObservation] = []
        for row in result.get("result", []):
            if isinstance(row, dict):
                observations.append(
                    EnergyObservation(
                        entity_id=_optional_str(row.get("entity_id")),
                        name=str(
                            row.get("name") or row.get("entity_id") or "Energy sensor"
                        ),
                        current_power_w=_float(row.get("value")),
                    )
                )
        return observations

    async def _mysql_energy_context(self) -> dict[str, Any]:
        try:
            async with mysql_session_context() as session:
                result = await session.execute(text("SELECT 1 AS available"))
                row = result.mappings().first()
                return dict(row) if row else {"available": True}
        except Exception:
            return {"available": False}

    async def _ha_state(self, entity_id: str) -> dict[str, Any] | None:
        settings = get_settings()
        if not settings.HA_TOKEN:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.HA_URL}/api/states/{entity_id}",
                    headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
                )
                if response.status_code == 200 and isinstance(response.json(), dict):
                    return response.json()
        except Exception:
            return None
        return None

    def _detect_anomalies(
        self,
        observations: list[EnergyObservation],
    ) -> list[EnergyObservation]:
        anomalies: list[EnergyObservation] = []
        for observation in observations:
            if observation.anomaly_reason:
                anomalies.append(observation)
                continue
            if not observation.baseline_w or observation.baseline_w <= 0:
                continue
            multiplier = observation.current_power_w / observation.baseline_w
            if (
                observation.current_power_w >= MEANINGFUL_POWER_WATTS
                and multiplier >= ANOMALY_MULTIPLIER_THRESHOLD
            ):
                anomalies.append(
                    observation.model_copy(
                        update={
                            "anomaly_reason": (
                                f"{observation.current_power_w:.0f}W is "
                                f"{multiplier:.1f}x baseline"
                            )
                        }
                    )
                )
        return anomalies

    def _detect_schedule_violations(
        self,
        observations: list[EnergyObservation],
    ) -> list[EnergyObservation]:
        return [
            observation
            for observation in observations
            if observation.scheduled is False
            and observation.current_power_w >= MEANINGFUL_POWER_WATTS
        ]

    def _build_recommendations(
        self,
        pricing: PricingSnapshot,
        observations: list[EnergyObservation],
        anomalies: list[EnergyObservation],
        schedule_violations: list[EnergyObservation],
    ) -> list[EnergyRecommendation]:
        recommendations: list[EnergyRecommendation] = []

        for observation in schedule_violations:
            recommendations.append(
                EnergyRecommendation(
                    priority="HIGH",
                    action=f"Turn off or reschedule {observation.name}",
                    reasoning=(
                        f"{observation.name} is drawing "
                        f"{observation.current_power_w:.0f}W outside its schedule"
                    ),
                )
            )

        for observation in anomalies:
            recommendations.append(
                EnergyRecommendation(
                    priority="HIGH",
                    action=f"Investigate abnormal energy use from {observation.name}",
                    reasoning=observation.anomaly_reason
                    or "Current power is far above baseline",
                )
            )

        total_power = sum(observation.current_power_w for observation in observations)
        if pricing.current_tier == "peak" and total_power >= MEANINGFUL_POWER_WATTS:
            recommendations.append(
                EnergyRecommendation(
                    priority="MEDIUM",
                    action="Delay flexible high-load tasks until after 9pm",
                    reasoning=(
                        f"Current pricing is peak at {pricing.cents_per_kwh:.0f}c/kWh "
                        f"with {total_power:.0f}W active load"
                    ),
                )
            )

        if not recommendations:
            recommendations.append(
                EnergyRecommendation(
                    priority="LOW",
                    action="Keep current schedule; review standby loads during the next cycle",
                    reasoning="No significant anomaly, peak-price load, or schedule violation was found",
                )
            )
        return recommendations

    def _build_alerts(
        self,
        pricing: PricingSnapshot,
        anomalies: list[EnergyObservation],
        schedule_violations: list[EnergyObservation],
    ) -> list[str]:
        alerts: list[str] = []
        if pricing.current_tier == "peak":
            alerts.append("Peak pricing is active; delay flexible loads if possible")
        for observation in anomalies:
            alerts.append(
                f"Energy anomaly: {observation.name} - {observation.anomaly_reason}"
            )
        for observation in schedule_violations:
            alerts.append(
                f"Schedule violation: {observation.name} is active off schedule"
            )
        return alerts

    async def _llm_recommendation(
        self,
        task: Task,
        pricing: PricingSnapshot,
        anomalies: list[EnergyObservation],
        schedule_violations: list[EnergyObservation],
        recommendations: list[EnergyRecommendation],
    ) -> EnergyRecommendation | None:
        if task.payload.get("use_llm") is not True or not PROMPT_PATH.exists():
            return None

        prompt = _render_prompt(
            PROMPT_PATH.read_text(),
            {
                "intent": task.intent,
                "pricing": pricing.model_dump(),
                "anomalies": [item.model_dump() for item in anomalies],
                "schedule_violations": [
                    item.model_dump() for item in schedule_violations
                ],
                "recommendations": [item.model_dump() for item in recommendations],
            },
        )
        try:
            raw = await self.llm.generate(prompt, temperature=0.2)
        except Exception:
            return None
        cleaned = raw.strip()
        if not cleaned:
            return None
        return EnergyRecommendation(
            priority="MEDIUM",
            action=cleaned[:240],
            reasoning="Generated from the energy prompt template",
        )

    def _mode(self, task: Task) -> str:
        if task.payload.get("type") == "energy" or "anomaly" in task.intent.lower():
            return "alert"
        return "routine"

    def _observation_from_mapping(self, raw: dict[str, Any]) -> EnergyObservation:
        return EnergyObservation(
            entity_id=_optional_str(raw.get("entity_id")),
            name=str(
                raw.get("name")
                or raw.get("device_name")
                or raw.get("entity_id")
                or "Energy signal"
            ),
            room=_optional_str(raw.get("room")),
            current_power_w=_float(raw.get("current_power_w") or raw.get("power")),
            baseline_w=_optional_float(raw.get("baseline_w") or raw.get("baseline")),
            scheduled=_optional_bool(raw.get("scheduled")),
            schedule=_optional_str(raw.get("schedule")),
            anomaly_reason=_optional_str(
                raw.get("anomaly_reason") or raw.get("reason")
            ),
        )


def _render_prompt(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", json.dumps(value, default=str))
        rendered = rendered.replace("{{" + key + "}}", json.dumps(value, default=str))
    return rendered


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return _float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)
