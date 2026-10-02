"""Human-readable energy analytics built from Home Assistant readings."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import get_settings
from orchestrator.core.database import get_mysql_session
from orchestrator.core.energy_analytics import rebuild_energy_analytics
from orchestrator.core.behavior_profile import build_behavior_profile
from orchestrator.core.ha_statistics import (
    CONTEXT_STATISTICS_SCHEMA,
    backfill_home_assistant_context_statistics,
    backfill_home_assistant_energy_statistics,
)
from orchestrator.core.irrigation import (
    IRRIGATION_RUNS_SCHEMA,
    backfill_home_assistant_irrigation_runs,
)
from orchestrator.core.weather import (
    HISTORICAL_WEATHER_SOURCE,
    ensure_weather_forecast_schema,
    fetch_historical_hourly_weather,
    store_hourly_forecast,
)
from orchestrator.llm.client import LLMClient

router = APIRouter(prefix="/analytics", tags=["analytics"])
settings = get_settings()


def _rows(result: Any) -> list[dict[str, Any]]:
    """Convert SQLAlchemy mappings to browser-safe records."""
    return jsonable_encoder([dict(row) for row in result.mappings().all()])


@router.get("", response_class=HTMLResponse)
async def analytics_page() -> HTMLResponse:
    """Serve the household energy analytics dashboard."""
    page = Path(__file__).resolve().parents[1] / "static" / "analytics.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/api/summary")
async def analytics_summary(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Return coverage and the latest computed analytics totals."""
    result = await session.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*) FROM energy_hourly_analytics) AS hourly_rows,
                (SELECT COUNT(DISTINCT device_id) FROM energy_hourly_analytics) AS measured_devices,
                (SELECT MIN(hour_start) FROM energy_hourly_analytics) AS analytics_start,
                (SELECT MAX(hour_start) FROM energy_hourly_analytics) AS analytics_end,
                (SELECT MAX(computed_at) FROM energy_hourly_analytics) AS last_computed_at,
                (SELECT COUNT(*) FROM sensor_readings) AS raw_readings,
                (SELECT MIN(timestamp) FROM sensor_readings) AS raw_start,
                (SELECT MAX(timestamp) FROM sensor_readings) AS raw_end,
                (SELECT COUNT(*) FROM home_events) AS home_event_count,
                (SELECT MAX(occurred_at) FROM home_events) AS newest_home_event
            """
        )
    )
    return jsonable_encoder(dict(result.mappings().one()))


@router.get("/api/insights")
async def learned_insights(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, list[dict[str, Any]]]:
    """Return evidence-backed adaptive insights without inventing preferences."""
    lookback_days = _analytics_lookback_days()
    await session.execute(text(CONTEXT_STATISTICS_SCHEMA))
    await session.execute(text(IRRIGATION_RUNS_SCHEMA))
    peak_result = await session.execute(
        text(
            f"""
            SELECT HOUR(hour_start) AS hour_of_day,
                   ROUND(AVG(reported_load_w), 1) AS avg_reported_load_w,
                   COUNT(*) AS observed_hours
            FROM (
                SELECT hour_start, SUM(avg_power_w) AS reported_load_w
                FROM energy_hourly_analytics
                WHERE hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
                GROUP BY hour_start
            ) AS hourly_load
            GROUP BY HOUR(hour_start)
            ORDER BY avg_reported_load_w DESC
            LIMIT 1
            """
        )
    )
    energy_coverage_result = await session.execute(
        text(
            f"""
            SELECT COUNT(DISTINCT DATE(hour_start)) AS observed_days,
                   MIN(hour_start) AS first_observed,
                   MAX(hour_start) AS last_observed
            FROM energy_hourly_analytics
            WHERE hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            """
        )
    )
    electrical_result = await session.execute(
        text(
            f"""
            SELECT d.name AS source, ROUND(MAX(eha.peak_power_w), 1) AS peak_power_w,
                   COUNT(*) AS observed_hours,
                   COUNT(DISTINCT DATE(eha.hour_start)) AS observed_days,
                   MIN(eha.hour_start) AS first_observed,
                   MAX(eha.hour_start) AS last_observed
            FROM energy_hourly_analytics eha
            JOIN devices d ON d.id = eha.device_id
            WHERE eha.hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
              AND eha.peak_power_w IS NOT NULL
            GROUP BY d.id, d.name
            ORDER BY peak_power_w DESC
            LIMIT 1
            """
        )
    )
    occupancy_result = await session.execute(
        text(
            """
            SELECT r.name AS room, ha.hour_of_day, ha.motion_probability,
                   JSON_EXTRACT(ha.weekly_pattern, '$.motion_on_events') AS motion_events
            FROM home_analytics ha
            JOIN rooms r ON r.id = ha.room_id
            WHERE ha.motion_probability > 0
            ORDER BY ha.motion_probability DESC, motion_events DESC
            LIMIT 1
            """
        )
    )
    learning_result = await session.execute(
        text(
            f"""
            SELECT
                (SELECT COUNT(*) FROM comfort_observations
                 WHERE observed_at >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)) AS comfort_observations,
                (SELECT COUNT(*) FROM comfort_observations
                 WHERE observed_at >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
                   AND target_changed = TRUE) AS target_changes,
                SUM(d.ha_entity_id LIKE 'weather.%') AS weather_readings,
                SUM(d.ha_entity_id LIKE 'valve.%'
                    OR d.ha_entity_id LIKE 'switch.%water%'
                    OR d.ha_entity_id LIKE 'switch.%sprinkler%'
                    OR d.ha_entity_id LIKE 'switch.%irrigation%') AS irrigation_readings
            FROM sensor_readings sr
            JOIN devices d ON d.id = sr.device_id
            WHERE sr.timestamp >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            """
        )
    )
    comfort_weather_result = await session.execute(
        text(
            f"""
            SELECT co.climate_entity_id,
                   MIN(co.observed_at) AS first_observed,
                   MAX(co.observed_at) AS last_observed,
                   COUNT(DISTINCT co.id) AS aligned_samples,
                   ROUND(AVG(co.target_temperature), 1) AS target_temperature_f,
                   ROUND(AVG(co.current_temperature), 1) AS room_temperature_f,
                   ROUND(AVG(wf.temperature_f), 1) AS outdoor_temperature_f
            FROM comfort_observations co
            JOIN weather_forecasts wf
              ON wf.forecast_at BETWEEN DATE_SUB(co.observed_at, INTERVAL 1 HOUR)
                 AND DATE_ADD(co.observed_at, INTERVAL 1 HOUR)
            WHERE co.observed_at >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
              AND co.target_temperature IS NOT NULL
              AND wf.temperature_f IS NOT NULL
              AND wf.source_entity_id <> :historical_weather_source
            GROUP BY co.climate_entity_id
            ORDER BY co.climate_entity_id
            """
        ),
        {"historical_weather_source": HISTORICAL_WEATHER_SOURCE},
    )
    irrigation_forecast_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*) AS forecast_observations,
                   COUNT(precipitation_in) AS precipitation_observations
            FROM weather_forecasts
            WHERE forecast_at >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
              AND source_entity_id <> :historical_weather_source
            """
        ),
        {"historical_weather_source": HISTORICAL_WEATHER_SOURCE},
    )
    irrigation_runs_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*) AS completed_runs,
                   COUNT(DISTINCT entity_id) AS zones,
                   MIN(started_at) AS first_run,
                   MAX(ended_at) AS last_run,
                   ROUND(SUM(duration_seconds) / 60, 1) AS total_minutes,
                   COUNT(outdoor_temperature_f) AS weather_matched_runs,
                   ROUND(AVG(outdoor_temperature_f), 1) AS average_temperature_f,
                   ROUND(SUM(CASE WHEN precipitation_in > 0 THEN 1 ELSE 0 END), 0) AS rainy_runs
            FROM irrigation_runs
            WHERE started_at >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            """
        )
    )
    temperature_history_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*) AS observations,
                   COUNT(DISTINCT statistic_id) AS sensors,
                   MIN(hour_start) AS first_observed,
                   MAX(hour_start) AS last_observed
            FROM home_assistant_hourly_statistics
            WHERE statistic_id LIKE '%_temperature'
              AND statistic_id NOT LIKE '%soil%'
              AND hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            """
        )
    )
    motion_telemetry_result = await session.execute(
        text(
            f"""
            SELECT COUNT(*) AS observations,
                   COUNT(DISTINCT statistic_id) AS sensors,
                   MIN(hour_start) AS first_observed,
                   MAX(hour_start) AS last_observed
            FROM home_assistant_hourly_statistics
            WHERE statistic_id LIKE '%motion_sensor%'
              AND hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            """
        )
    )
    historical_weather_result = await session.execute(
        text(
            f"""
            SELECT hs.statistic_id,
                   COUNT(*) AS matched_hours,
                   ROUND(AVG(hs.mean_value), 1) AS indoor_temperature_f,
                   ROUND(AVG(wf.temperature_f), 1) AS outdoor_temperature_f,
                   MIN(hs.hour_start) AS first_observed,
                   MAX(hs.hour_start) AS last_observed
            FROM home_assistant_hourly_statistics hs
            JOIN weather_forecasts wf
              ON wf.source_entity_id = :historical_weather_source
             AND wf.forecast_at = hs.hour_start
            WHERE hs.statistic_id LIKE '%_temperature'
              AND hs.statistic_id NOT LIKE '%soil%'
              AND hs.hour_start >= DATE_SUB(NOW(), INTERVAL {lookback_days} DAY)
            GROUP BY hs.statistic_id
            ORDER BY hs.statistic_id
            """
        ),
        {"historical_weather_source": HISTORICAL_WEATHER_SOURCE},
    )
    peak = peak_result.mappings().first()
    energy_coverage = dict(energy_coverage_result.mappings().one())
    electrical = electrical_result.mappings().first()
    occupancy = occupancy_result.mappings().first()
    learning = learning_result.mappings().one()
    comfort_weather = [dict(row) for row in comfort_weather_result.mappings().all()]
    irrigation_forecast = irrigation_forecast_result.mappings().one()
    irrigation_runs = dict(irrigation_runs_result.mappings().one())
    temperature_history = dict(temperature_history_result.mappings().one())
    motion_telemetry = dict(motion_telemetry_result.mappings().one())
    historical_weather = [dict(row) for row in historical_weather_result.mappings().all()]

    insights: list[dict[str, Any]] = []
    if peak is not None:
        hour = int(peak["hour_of_day"])
        confidence = min(0.9, 0.45 + min(int(peak["observed_hours"]), 28) / 62)
        insights.append(
            {
                "category": "Energy behaviour",
                "state": "learned",
                "confidence": confidence,
                "headline": f"Recurring demand is highest around {_hour_label(hour)}.",
                "detail": (
                    f"Across {int(energy_coverage['observed_days'] or 0)} observed days, from "
                    f"{_short_date(energy_coverage['first_observed'])} through "
                    f"{_short_date(energy_coverage['last_observed'])}, reporting sources "
                    f"averaged {float(peak['avg_reported_load_w']):,.0f} W at 10 PM "
                    f"({int(peak['observed_hours'])} recorded 10 PM samples). "
                    "EcoNest can use this pattern to time flexible-load recommendations."
                ),
                "next_step": "Add the household electricity plan before turning this into a cost-saving recommendation.",
            }
        )
    else:
        insights.append(_waiting_insight("Energy behaviour", "No usable hourly power pattern yet."))

    if electrical is not None:
        insights.append(
            {
                "category": "Electrical load",
                "state": "observed",
                "confidence": 0.8,
                "headline": "Highest observed source peak identified.",
                "detail": (
                    f"{electrical['source']} reached {float(electrical['peak_power_w']):,.0f} W "
                    f"across {int(electrical['observed_days'])} observed days, from "
                    f"{_short_date(electrical['first_observed'])} through "
                    f"{_short_date(electrical['last_observed'])} "
                    f"({int(electrical['observed_hours'])} measured hours)."
                ),
                "next_step": "Add each circuit's rated capacity before EcoNest makes overload or component-health recommendations.",
            }
        )
    else:
        insights.append(_waiting_insight("Electrical load", "No compatible power-source history yet."))

    if occupancy is not None:
        insights.append(
            {
                "category": "Room activity",
                "state": "learned",
                "confidence": min(0.75, float(occupancy["motion_probability"]) + 0.2),
                "headline": f"{occupancy['room']} shows its strongest motion activity around {_hour_label(int(occupancy['hour_of_day']))}.",
                "detail": "This is an activity-based occupancy signal derived from motion events. It indicates room activity, not proof that a specific person is home.",
                "next_step": "EcoNest can combine this signal with thermostat choices and room conditions as comfort-preference evidence accumulates.",
            }
        )
    else:
        insights.append(
            {
                "category": "Room activity",
                "state": "needs_data",
                "confidence": 0,
                "headline": "No historical room-activity pattern is available yet.",
                "detail": _motion_telemetry_detail(motion_telemetry),
                "next_step": "Restore the Zigbee connection for at least one motion sensor; EcoNest will then begin collecting room-activity signals automatically.",
            }
        )

    comfort_observations = int(learning["comfort_observations"] or 0)
    target_changes = int(learning["target_changes"] or 0)
    if comfort_weather:
        aligned_samples = sum(int(row["aligned_samples"] or 0) for row in comfort_weather)
        preference_confidence = min(0.55, 0.2 + aligned_samples / 2_000)
        insights.append(
            {
                "category": "Comfort preferences",
                "state": "learning",
                "confidence": preference_confidence,
                "confidence_label": "early-baseline confidence",
                "headline": "Historical room-temperature baselines and early weather-aware comfort signals are available.",
                "detail": _comfort_weather_detail(
                    comfort_weather,
                    temperature_history,
                    historical_weather,
                ),
                "next_step": (
                    "EcoNest will keep comparing room setpoints with forecast outdoor "
                    "conditions. Manual target changes across different weather will turn "
                    "these early baselines into stronger room-specific preferences."
                ),
            }
        )
    else:
        preference_confidence = min(0.85, target_changes / 20) if target_changes else 0.0
        insights.append(
            {
                "category": "Comfort preferences",
                "state": "learning" if target_changes else "needs_data",
                "confidence": preference_confidence,
                "confidence_label": "preference confidence",
                "headline": (
                    "No weather-aligned temperature baseline is available yet."
                    if target_changes == 0
                    else "Building a temperature-preference pattern."
                ),
                "detail": (
                    f"EcoNest has {comfort_observations} room-condition snapshots, but "
                    f"no matching forecast-weather observations yet."
                ),
                "next_step": "Keep Home Assistant ingestion running so EcoNest can align room conditions with weather data.",
            }
        )

    weather_readings = int(learning["weather_readings"] or 0)
    irrigation_readings = int(learning["irrigation_readings"] or 0)
    forecast_observations = int(irrigation_forecast["forecast_observations"] or 0)
    precipitation_observations = int(
        irrigation_forecast["precipitation_observations"] or 0
    )
    completed_runs = int(irrigation_runs["completed_runs"] or 0)
    insights.append(
        {
            "category": "Sprinkler recommendations",
            "state": "learning",
            "confidence": min(0.7, 0.15 + min(forecast_observations, 72) / 360 + min(completed_runs, 25) / 50),
            "confidence_label": "early irrigation evidence",
            "headline": "Early weather and irrigation observations are available.",
            "detail": _irrigation_detail(
                forecast_observations, precipitation_observations, weather_readings,
                irrigation_readings, irrigation_runs,
            ),
            "next_step": (
                "EcoNest can provide forecast-based, recommendation-only watering reviews now. "
                "It will strengthen zone-specific patterns as more completed watering runs are retained."
            ),
        }
    )
    insights.append(_electricity_cost_insight(settings.HOUSEHOLD_ELECTRICITY_PROVIDER))
    return {"insights": insights}


@router.post("/api/behavior-profile")
async def behavior_profile(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Rebuild the evidence-backed household behavior profile for Insights."""
    return jsonable_encoder(await build_behavior_profile(session))


@router.post("/api/model-explanation")
async def model_explanation(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, str]:
    """Ask the local model to explain the current calculated analytics evidence."""
    evidence = await learned_insights(session)
    prompt = _analytics_narrative_prompt(evidence["insights"])
    client = LLMClient()
    try:
        narrative = await client.generate(
            prompt,
            system=(
                "You are EcoNest's cautious household analytics explainer. "
                "Use only the supplied calculated evidence. Never invent measurements, "
                "preferences, device activity, savings, risks, or recommendations."
            ),
            temperature=0.1,
            max_retries=1,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local analytics model is unavailable. The calculated evidence remains available.",
        ) from exc
    finally:
        await client.close()
    cleaned = " ".join(narrative.split())
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The local analytics model returned no explanation. The calculated evidence remains available.",
        )
    return {
        "narrative": cleaned[:2_000],
        "source": settings.OLLAMA_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _waiting_insight(category: str, detail: str) -> dict[str, Any]:
    """Return a transparent no-data insight instead of a fabricated conclusion."""
    return {
        "category": category,
        "state": "needs_data",
        "confidence": 0,
        "headline": "Not enough evidence yet.",
        "detail": detail,
        "next_step": "Keep Home Assistant ingestion running so EcoNest can establish a baseline.",
    }


def _analytics_lookback_days() -> int:
    """Keep analytics history configurable while bounding SQL interpolation."""
    return min(max(settings.ANALYTICS_LOOKBACK_DAYS, 1), 3_650)


def _hour_label(hour: int) -> str:
    """Format a 24-hour value for a household-facing insight."""
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour} {suffix}"


def _comfort_weather_detail(
    rows: list[dict[str, Any]],
    temperature_history: dict[str, Any],
    historical_weather: list[dict[str, Any]],
) -> str:
    """Format weather-aligned room setpoints as an explainable early baseline."""
    descriptions: list[str] = []
    for row in rows:
        entity_id = str(row.get("climate_entity_id", ""))
        room = (
            entity_id.removeprefix("climate.")
            .replace("_", " ")
            .title()
            .replace(" And ", " and ")
        )
        samples = int(row.get("aligned_samples") or 0)
        target = float(row.get("target_temperature_f") or 0)
        outdoor = float(row.get("outdoor_temperature_f") or 0)
        descriptions.append(
            f"{room}: {target:g}°F setpoint across {samples} observations "
            f"while the forecast outdoor temperature averaged {outdoor:g}°F"
        )
    historical_detail = _temperature_history_detail(temperature_history, historical_weather)
    return (
        historical_detail
        + " "
        "By comparing retained thermostat observations with the outdoor forecast, "
        "EcoNest has learned these early room baselines: "
        + "; ".join(descriptions)
        + "."
    )


def _temperature_history_detail(
    history: dict[str, Any],
    historical_weather: list[dict[str, Any]],
) -> str:
    """Explain what retained temperature history can and cannot establish."""
    observations = int(history.get("observations") or 0)
    sensors = int(history.get("sensors") or 0)
    first = history.get("first_observed")
    last = history.get("last_observed")
    if not observations or not first or not last:
        return "No historical room-temperature statistics are retained yet."
    detail = (
        f"Home Assistant retained {observations:,} hourly room-temperature observations across "
        f"{sensors} sensors from {_short_date(first)} through {_short_date(last)}. "
        "They show room conditions, but do not preserve historical thermostat target changes."
    )
    if not historical_weather:
        return detail
    comparisons: list[str] = []
    for row in historical_weather:
        entity_id = str(row.get("statistic_id") or "")
        room = entity_id.removeprefix("sensor.").removesuffix("_temperature").replace("_", " ").title()
        room = room.replace(" And ", " and ")
        matched = int(row.get("matched_hours") or 0)
        indoor = float(row.get("indoor_temperature_f") or 0)
        outdoor = float(row.get("outdoor_temperature_f") or 0)
        comparisons.append(
            f"{room}: {indoor:g}°F indoors while archived outdoor temperature averaged {outdoor:g}°F across {matched:,} hours"
        )
    return detail + " Historical weather matches show " + "; ".join(comparisons) + "."


def _motion_telemetry_detail(history: dict[str, Any]) -> str:
    """Separate motion-device health telemetry from actual motion events."""
    observations = int(history.get("observations") or 0)
    sensors = int(history.get("sensors") or 0)
    first = history.get("first_observed")
    last = history.get("last_observed")
    if not observations or not first or not last:
        return (
            "EcoNest has no retained binary motion-event history because the configured "
            "Zigbee motion sensors are currently unavailable in Home Assistant."
        )
    return (
        "The live Garage ZHA motion sensor (HOBEIAN ZG-204ZL) is currently unavailable. "
        f"Home Assistant retained {observations:,} hourly garage motion-device telemetry observations "
        f"across {sensors} sources from {_short_date(first)} through {_short_date(last)}. "
        "These are illuminance and battery measurements, not binary motion events, so they cannot "
        "be used to infer room activity; the historical motion events were purged from Home Assistant's "
        "short-term recorder."
    )


def _irrigation_detail(
    forecast_observations: int,
    precipitation_observations: int,
    weather_readings: int,
    irrigation_readings: int,
    runs: dict[str, Any],
) -> str:
    """Describe only retained irrigation and weather evidence."""
    completed = int(runs.get("completed_runs") or 0)
    zones = int(runs.get("zones") or 0)
    total_minutes = float(runs.get("total_minutes") or 0)
    matched = int(runs.get("weather_matched_runs") or 0)
    average_temperature = runs.get("average_temperature_f")
    rainy = int(runs.get("rainy_runs") or 0)
    base = (
        f"EcoNest has {forecast_observations} hourly forecast records "
        f"({precipitation_observations} with precipitation data), {weather_readings} Home Assistant weather updates, "
        f"and {irrigation_readings} recent irrigation-zone state snapshots."
    )
    if not completed:
        return base + " No completed historical watering runs have been imported yet."
    run_detail = (
        f" Home Assistant history also retained {completed} completed manual watering runs across "
        f"{zones} zones, totalling {total_minutes:g} minutes"
    )
    if matched and average_temperature is not None:
        run_detail += f". Archived weather matched {matched} runs; the average outdoor temperature was {float(average_temperature):g}°F"
        if rainy:
            run_detail += f", with precipitation recorded during {rainy} run{'s' if rainy != 1 else ''}"
        else:
            run_detail += "; no precipitation was recorded at the start of those runs"
        run_detail += "."
    else:
        run_detail += ". Weather matching is pending."
    return base + run_detail


def _analytics_narrative_prompt(insights: list[dict[str, Any]]) -> str:
    """Build a bounded, evidence-only prompt for the local analytics model."""
    evidence = [
        {
            "category": insight.get("category"),
            "state": insight.get("state"),
            "headline": insight.get("headline"),
            "detail": insight.get("detail"),
            "next_step": insight.get("next_step"),
        }
        for insight in insights
    ]
    return (
        "Write a short, readable analytics briefing in two compact paragraphs. "
        "First summarize the strongest available observations. Then state the most important "
        "evidence gaps or safe next steps. Do not propose an action, claim a preference, or add "
        "any fact not present in the evidence. Do not mention JSON, prompts, or being a model.\n\n"
        "Calculated EcoNest evidence:\n"
        + json.dumps(evidence, ensure_ascii=False)
    )


def _short_date(value: Any) -> str:
    """Render database timestamps for household-facing explanatory text."""
    if isinstance(value, datetime):
        return f"{value.strftime('%b')} {value.day}, {value.year}"
    return str(value)


def _electricity_cost_insight(provider: str) -> dict[str, Any]:
    """Describe cost-learning readiness without assuming an unverified rate."""
    normalized_provider = provider.strip()
    if normalized_provider.lower() == "college station utilities":
        return {
            "category": "Texas electricity cost",
            "state": "learning",
            "confidence": 0.2,
            "confidence_label": "provider configuration",
            "headline": "College Station Utilities is configured as the household provider.",
            "detail": (
                "EcoNest treats College Station Utilities as the municipal electricity provider. "
                "Its residential pricing is seasonal rather than time-of-use, so EcoNest will not label "
                "particular hours of the day as cheaper."
            ),
            "next_step": (
                "Add the current CSU residential rate schedule or a redacted electric-bill line item "
                "before EcoNest estimates dollars or compares seasonal electricity costs."
            ),
        }
    if normalized_provider:
        return {
            "category": "Texas electricity cost",
            "state": "learning",
            "confidence": 0.2,
            "confidence_label": "provider configuration",
            "headline": f"{normalized_provider} is configured as the household provider.",
            "detail": "EcoNest knows the provider but does not yet have the household's applicable electricity rate schedule.",
            "next_step": "Add the current rate schedule or a redacted electric-bill line item before EcoNest estimates dollars or labels a time as cheaper.",
        }
    return {
        "category": "Texas electricity cost",
        "state": "needs_setup",
        "confidence": 0,
        "headline": "Household electricity provider is not configured.",
        "detail": "EcoNest needs the household's electricity provider and applicable rate schedule before it can estimate costs.",
        "next_step": "Configure the provider, then add the current rate schedule or a redacted electric-bill line item.",
    }


@router.get("/api/timeline")
async def hourly_timeline(
    session: AsyncSession = Depends(get_mysql_session),
    hours: Annotated[int, Query(ge=24, le=24 * 90)] = 24 * 7,
) -> dict[str, list[dict[str, Any]]]:
    """Return household reported load and measured energy by hour."""
    result = await session.execute(
        text(
            """
            SELECT
                hour_start,
                ROUND(SUM(avg_power_w), 1) AS reported_avg_power_w,
                ROUND(MAX(peak_power_w), 1) AS highest_device_power_w,
                ROUND(SUM(metered_energy_kwh), 4) AS metered_energy_kwh,
                COUNT(DISTINCT device_id) AS contributing_devices
            FROM energy_hourly_analytics
            WHERE hour_start >= DATE_SUB(NOW(), INTERVAL :hours HOUR)
            GROUP BY hour_start
            ORDER BY hour_start ASC
            """
        ),
        {"hours": hours},
    )
    return {"hours": _rows(result)}


@router.get("/api/peak-hours")
async def peak_hours(
    session: AsyncSession = Depends(get_mysql_session),
    days: Annotated[int, Query(ge=1, le=90)] = 28,
) -> dict[str, list[dict[str, Any]]]:
    """Return recurring high-energy clock hours within the selected window."""
    result = await session.execute(
        text(
            """
            SELECT
                HOUR(hour_start) AS hour_of_day,
                ROUND(SUM(metered_energy_kwh), 4) AS metered_energy_kwh,
                ROUND(AVG(reported_load_w), 1) AS avg_reported_load_w,
                COUNT(*) AS measured_hours
            FROM (
                SELECT
                    hour_start,
                    SUM(avg_power_w) AS reported_load_w,
                    SUM(metered_energy_kwh) AS metered_energy_kwh
                FROM energy_hourly_analytics
                WHERE hour_start >= DATE_SUB(NOW(), INTERVAL :days DAY)
                GROUP BY hour_start
            ) AS household_hour
            GROUP BY HOUR(hour_start)
            ORDER BY metered_energy_kwh DESC, avg_reported_load_w DESC
            LIMIT 6
            """
        ),
        {"days": days},
    )
    return {"hours": _rows(result)}


@router.get("/api/devices")
async def device_energy(
    session: AsyncSession = Depends(get_mysql_session),
    days: Annotated[int, Query(ge=1, le=90)] = 28,
) -> dict[str, list[dict[str, Any]]]:
    """Return monitored devices/circuits with the highest observed usage."""
    result = await session.execute(
        text(
            """
            SELECT
                d.name AS device,
                d.ha_entity_id,
                r.name AS room,
                ROUND(SUM(eha.metered_energy_kwh), 4) AS metered_energy_kwh,
                ROUND(MAX(eha.peak_power_w), 1) AS peak_power_w,
                ROUND(AVG(eha.avg_power_w), 1) AS avg_power_w,
                COUNT(*) AS measured_hours
            FROM energy_hourly_analytics eha
            JOIN devices d ON d.id = eha.device_id
            JOIN rooms r ON r.id = eha.room_id
            WHERE eha.hour_start >= DATE_SUB(NOW(), INTERVAL :days DAY)
            GROUP BY d.id, d.name, d.ha_entity_id, r.name
            ORDER BY metered_energy_kwh DESC, peak_power_w DESC
            LIMIT 12
            """
        ),
        {"days": days},
    )
    return {"devices": _rows(result)}


@router.post("/api/rebuild")
async def rebuild(
    session: AsyncSession = Depends(get_mysql_session),
    days: Annotated[int, Query(ge=1, le=90)] = 45,
) -> dict[str, Any]:
    """Rebuild a bounded analytics window from retained readings."""
    try:
        return await rebuild_energy_analytics(session, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/api/backfill-home-assistant-statistics")
async def backfill_home_assistant_statistics(
    session: AsyncSession = Depends(get_mysql_session),
    start: datetime = Query(default=datetime(2026, 4, 1, tzinfo=UTC)),
) -> dict[str, Any]:
    """Import retained hourly Home Assistant energy statistics into analytics."""
    try:
        energy = await backfill_home_assistant_energy_statistics(
            session,
            settings,
            start=start,
        )
        context = await backfill_home_assistant_context_statistics(
            session,
            settings,
            start=start,
        )
        return {"energy": energy, "context": context}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/api/backfill-historical-weather")
async def backfill_historical_weather(
    session: AsyncSession = Depends(get_mysql_session),
    start: date = Query(default=date(2026, 4, 1)),
    end: date | None = Query(default=None),
) -> dict[str, Any]:
    """Store archived hourly outdoor conditions for historical comfort analysis."""
    resolved_end = end or (datetime.now(UTC).date() - timedelta(days=1))
    try:
        await ensure_weather_forecast_schema(session)
        weather = await fetch_historical_hourly_weather(
            settings,
            start_date=start,
            end_date=resolved_end,
        )
        stored = await store_hourly_forecast(session, HISTORICAL_WEATHER_SOURCE, weather)
        await session.commit()
        return {
            "source": HISTORICAL_WEATHER_SOURCE,
            "start": start.isoformat(),
            "end": resolved_end.isoformat(),
            "rows_upserted": stored,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/api/backfill-home-assistant-irrigation")
async def backfill_home_assistant_irrigation(
    session: AsyncSession = Depends(get_mysql_session),
    start: datetime = Query(default=datetime(2026, 4, 1, tzinfo=UTC)),
    end: datetime | None = Query(default=None),
) -> dict[str, Any]:
    """Import completed historic watering runs and align them with archived weather."""
    try:
        return await backfill_home_assistant_irrigation_runs(
            session,
            settings,
            start=start,
            end=end or datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
