"""Energy optimization agent."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.config import get_settings
from orchestrator.core.database import arcadedb_query, mysql_session_context

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "energy.j2"

ANOMALY_MULTIPLIER_THRESHOLD = 4.0
MEANINGFUL_POWER_WATTS = 100.0


class PricingSnapshot(BaseModel):
    """Pricing inference, sourced from a tariff forecast when one is available."""

    current_tier: str
    cents_per_kwh: float | None = None
    source: str
    next_cheap_window: str | None = None


class TariffForecastWindow(BaseModel):
    """A utility, provider, or upstream-model price forecast window."""

    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)
    cents_per_kwh: float = Field(ge=0)


class EnergyHistorySample(BaseModel):
    """One historical measurement used to learn demand and routines."""

    name: str
    current_power_w: float = Field(ge=0)
    hour: int = Field(ge=0, le=23)
    flexible: bool = False


class HouseholdRoutine(BaseModel):
    """A recurring appliance-use pattern inferred from supplied history."""

    name: str
    sample_count: int
    common_hours: list[int]
    average_power_w: float
    flexible: bool


class LoadForecast(BaseModel):
    """Short-term demand forecast learned from historical measurements."""

    hour: int = Field(ge=0, le=23)
    expected_power_w: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


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
    recommendation_only: bool = True
    household_routines: list[HouseholdRoutine] = Field(default_factory=list)
    demand_forecast: list[LoadForecast] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class EnergyAgent(BaseAgent):
    """Responsibilities: power optimization, TOU pricing, schedules, anomalies."""

    name = "energy"
    # The energy agent is deliberately advisory. Device control belongs to DeviceAgent.
    tools = ["query_mysql", "query_arcadedb"]
    permissions = ["device:read", "agent:run"]

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
        observations = self._observations_from_payload(task.payload)
        observations.extend(await self._observations_from_graph())
        history = self._history_from_payload(task.payload)
        history.extend(await self._history_from_mysql())
        routines = self._learn_household_routines(history)
        forecast = self._forecast_demand(history)
        pricing = self._pricing_from_forecast(
            context["current_hour"], task.payload, forecast
        )

        anomalies = self._detect_anomalies(observations)
        schedule_violations = self._detect_schedule_violations(observations)
        recommendations = self._build_recommendations(
            pricing,
            observations,
            anomalies,
            schedule_violations,
            routines,
            forecast,
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
            household_routines=routines,
            demand_forecast=forecast,
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

    def _pricing_from_forecast(
        self,
        hour: int,
        payload: dict[str, Any],
        demand_forecast: list[LoadForecast],
    ) -> PricingSnapshot:
        """Use incoming tariff data; never invent a fixed utility schedule."""
        windows = self._tariff_windows_from_payload(payload)
        if windows:
            current = next(
                (window for window in windows if _hour_in_window(hour, window)), None
            )
            cheapest = min(windows, key=lambda window: window.cents_per_kwh)
            highest_price = max(window.cents_per_kwh for window in windows)
            if current is None:
                return PricingSnapshot(
                    current_tier="unknown",
                    source="tariff_forecast",
                    next_cheap_window=_window_label(cheapest),
                )
            return PricingSnapshot(
                current_tier=(
                    "peak"
                    if highest_price > cheapest.cents_per_kwh
                    and current.cents_per_kwh == highest_price
                    else "off_peak"
                ),
                cents_per_kwh=current.cents_per_kwh,
                source="tariff_forecast",
                next_cheap_window=_window_label(cheapest),
            )

        next_low_demand = min(
            demand_forecast,
            key=lambda item: item.expected_power_w,
            default=None,
        )
        return PricingSnapshot(
            current_tier="unknown",
            source="no_tariff_forecast",
            next_cheap_window=(
                f"around {_hour_label(next_low_demand.hour)} (lowest learned demand)"
                if next_low_demand
                else None
            ),
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

    async def _history_from_mysql(self) -> list[EnergyHistorySample]:
        """Learn from retained readings without requiring a fixed schedule."""
        settings = get_settings()
        try:
            async with mysql_session_context() as session:
                result = await session.execute(
                    text(
                        "SELECT d.name, sr.timestamp, sr.data "
                        "FROM sensor_readings sr "
                        "JOIN devices d ON d.id = sr.device_id "
                        "WHERE sr.timestamp >= NOW() - "
                        "INTERVAL :lookback_days DAY "
                        "ORDER BY sr.timestamp DESC LIMIT :sample_limit"
                    ),
                    {
                        "lookback_days": settings.ENERGY_HISTORY_LOOKBACK_DAYS,
                        "sample_limit": settings.ENERGY_HISTORY_MAX_SAMPLES,
                    },
                )
                rows = result.mappings().all()
        except Exception:
            return []

        history: list[EnergyHistorySample] = []
        for row in rows:
            data = _json_mapping(row.get("data"))
            power = (
                data.get("current_power_w") or data.get("power_w") or data.get("power")
            )
            timestamp = row.get("timestamp")
            if power is None or not isinstance(timestamp, datetime):
                continue
            try:
                history.append(
                    EnergyHistorySample(
                        name=str(row.get("name") or "Energy device"),
                        current_power_w=_float(power),
                        hour=timestamp.hour,
                        flexible=_optional_bool(data.get("flexible")) is True,
                    )
                )
            except ValueError:
                continue
        return history

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
        routines: list[HouseholdRoutine],
        forecast: list[LoadForecast],
    ) -> list[EnergyRecommendation]:
        recommendations: list[EnergyRecommendation] = []

        for observation in schedule_violations:
            recommendations.append(
                EnergyRecommendation(
                    priority="HIGH",
                    action=f"Review or reschedule {observation.name}",
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
        flexible_routines = [routine for routine in routines if routine.flexible]
        if (
            pricing.current_tier == "peak"
            and total_power >= MEANINGFUL_POWER_WATTS
            and flexible_routines
        ):
            recommendations.append(
                EnergyRecommendation(
                    priority="MEDIUM",
                    action=(
                        "Schedule flexible loads for "
                        f"{pricing.next_cheap_window or 'the next lower-price window'}"
                    ),
                    reasoning=(
                        f"The tariff forecast is {pricing.cents_per_kwh:.0f}c/kWh now "
                        f"with {total_power:.0f}W active load"
                    ),
                )
            )

        if pricing.current_tier == "unknown" and flexible_routines and forecast:
            lowest = min(forecast, key=lambda item: item.expected_power_w)
            recommendations.append(
                EnergyRecommendation(
                    priority="LOW",
                    action=(
                        f"Consider running flexible loads around {_hour_label(lowest.hour)}"
                    ),
                    reasoning=(
                        "No tariff forecast is available; this is the lowest-demand "
                        "period learned from household history."
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
            alerts.append(
                "Tariff forecast indicates the current price is at its highest"
            )
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

    def _history_from_payload(
        self, payload: dict[str, Any]
    ) -> list[EnergyHistorySample]:
        history: list[EnergyHistorySample] = []
        for raw in payload.get("history", []):
            if not isinstance(raw, dict):
                continue
            try:
                history.append(EnergyHistorySample.model_validate(raw))
            except ValueError:
                continue
        return history

    def _tariff_windows_from_payload(
        self, payload: dict[str, Any]
    ) -> list[TariffForecastWindow]:
        windows: list[TariffForecastWindow] = []
        for raw in payload.get("tariff_forecast", []):
            if not isinstance(raw, dict):
                continue
            try:
                windows.append(TariffForecastWindow.model_validate(raw))
            except ValueError:
                continue
        return windows

    def _learn_household_routines(
        self, history: list[EnergyHistorySample]
    ) -> list[HouseholdRoutine]:
        grouped: dict[str, list[EnergyHistorySample]] = defaultdict(list)
        for sample in history:
            if sample.current_power_w >= MEANINGFUL_POWER_WATTS:
                grouped[sample.name].append(sample)
        routines: list[HouseholdRoutine] = []
        for name, samples in grouped.items():
            counts: dict[int, int] = defaultdict(int)
            for sample in samples:
                counts[sample.hour] += 1
            common_hours = [
                hour
                for hour, _ in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )[:3]
            ]
            routines.append(
                HouseholdRoutine(
                    name=name,
                    sample_count=len(samples),
                    common_hours=common_hours,
                    average_power_w=(
                        sum(item.current_power_w for item in samples) / len(samples)
                    ),
                    flexible=all(item.flexible for item in samples),
                )
            )
        return sorted(routines, key=lambda routine: routine.name)

    def _forecast_demand(
        self, history: list[EnergyHistorySample]
    ) -> list[LoadForecast]:
        by_hour: dict[int, list[float]] = defaultdict(list)
        for sample in history:
            by_hour[sample.hour].append(sample.current_power_w)
        return [
            LoadForecast(
                hour=hour,
                expected_power_w=sum(values) / len(values),
                confidence=min(1.0, len(values) / 7),
            )
            for hour, values in sorted(by_hour.items())
        ]


def _render_prompt(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", json.dumps(value, default=str))
        rendered = rendered.replace("{{" + key + "}}", json.dumps(value, default=str))
    return rendered


def _hour_in_window(hour: int, window: TariffForecastWindow) -> bool:
    """Check a same-day or overnight hourly tariff window."""
    if window.start_hour < window.end_hour:
        return window.start_hour <= hour < window.end_hour
    return hour >= window.start_hour or hour < window.end_hour


def _hour_label(hour: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    return f"{display_hour}{suffix}"


def _window_label(window: TariffForecastWindow) -> str:
    return f"{_hour_label(window.start_hour)}–{_hour_label(window.end_hour % 24)}"


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
