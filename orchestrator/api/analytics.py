"""Human-readable energy analytics built from Home Assistant readings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.database import get_mysql_session
from orchestrator.core.energy_analytics import rebuild_energy_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


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
                (SELECT MAX(timestamp) FROM sensor_readings) AS raw_end
            """
        )
    )
    return jsonable_encoder(dict(result.mappings().one()))


@router.get("/api/insights")
async def learned_insights(
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, list[dict[str, Any]]]:
    """Return evidence-backed adaptive insights without inventing preferences."""
    peak_result = await session.execute(
        text(
            """
            SELECT HOUR(hour_start) AS hour_of_day,
                   ROUND(AVG(reported_load_w), 1) AS avg_reported_load_w,
                   COUNT(*) AS observed_hours
            FROM (
                SELECT hour_start, SUM(avg_power_w) AS reported_load_w
                FROM energy_hourly_analytics
                WHERE hour_start >= DATE_SUB(NOW(), INTERVAL 28 DAY)
                GROUP BY hour_start
            ) AS hourly_load
            GROUP BY HOUR(hour_start)
            ORDER BY avg_reported_load_w DESC
            LIMIT 1
            """
        )
    )
    electrical_result = await session.execute(
        text(
            """
            SELECT d.name AS source, ROUND(MAX(eha.peak_power_w), 1) AS peak_power_w,
                   COUNT(*) AS observed_hours
            FROM energy_hourly_analytics eha
            JOIN devices d ON d.id = eha.device_id
            WHERE eha.hour_start >= DATE_SUB(NOW(), INTERVAL 28 DAY)
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
            """
            SELECT
                (SELECT COUNT(*) FROM comfort_observations
                 WHERE observed_at >= DATE_SUB(NOW(), INTERVAL 28 DAY)) AS comfort_observations,
                (SELECT COUNT(*) FROM comfort_observations
                 WHERE observed_at >= DATE_SUB(NOW(), INTERVAL 28 DAY)
                   AND target_changed = TRUE) AS target_changes,
                SUM(d.ha_entity_id LIKE 'weather.%') AS weather_readings,
                SUM(d.ha_entity_id LIKE 'valve.%'
                    OR d.ha_entity_id LIKE 'switch.%water%'
                    OR d.ha_entity_id LIKE 'switch.%sprinkler%'
                    OR d.ha_entity_id LIKE 'switch.%irrigation%') AS irrigation_readings
            FROM sensor_readings sr
            JOIN devices d ON d.id = sr.device_id
            WHERE sr.timestamp >= DATE_SUB(NOW(), INTERVAL 28 DAY)
            """
        )
    )
    peak = peak_result.mappings().first()
    electrical = electrical_result.mappings().first()
    occupancy = occupancy_result.mappings().first()
    learning = learning_result.mappings().one()

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
                    f"Across {int(peak['observed_hours'])} observed days, reporting sources "
                    f"averaged {float(peak['avg_reported_load_w']):,.0f} W at that hour. "
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
                    f"in the last 28 days ({int(electrical['observed_hours'])} measured hours)."
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
                "headline": "No room-activity pattern is available yet.",
                "detail": "EcoNest has no usable motion history because the two configured Zigbee motion sensors are currently unavailable in Home Assistant.",
                "next_step": "Restore the Zigbee connection for at least one motion sensor; EcoNest will then begin collecting room-activity signals automatically.",
            }
        )

    comfort_observations = int(learning["comfort_observations"] or 0)
    target_changes = int(learning["target_changes"] or 0)
    preference_confidence = min(0.85, target_changes / 20) if target_changes else 0.0
    insights.append(
        {
            "category": "Comfort preferences",
            "state": "learning" if target_changes else "needs_data",
            "confidence": preference_confidence,
            "confidence_label": "preference confidence",
            "headline": (
                "No temperature-preference signal has been observed yet."
                if target_changes == 0
                else "Building a temperature-preference pattern."
            ),
            "detail": (
                f"EcoNest has {comfort_observations} room-condition snapshots, but "
                f"{target_changes} target-temperature changes in the last 28 days. "
                "Snapshots provide context; only repeated temperature choices can demonstrate a preference."
            ),
            "next_step": (
                "Continue using the thermostat normally. Once target changes occur across different times and conditions, EcoNest can begin offering recommendation-only comfort suggestions."
                if target_changes == 0
                else "EcoNest is still recommendation-only until it has enough repeated choices across different times and room conditions."
            ),
        }
    )

    weather_readings = int(learning["weather_readings"] or 0)
    irrigation_readings = int(learning["irrigation_readings"] or 0)
    insights.append(
        {
            "category": "Sprinkler recommendations",
            "state": "needs_data",
            "confidence": 0,
            "confidence_label": "irrigation-decision confidence",
            "headline": (
                "No irrigation recommendation is available yet."
            ),
            "detail": (
                f"EcoNest currently has {weather_readings} weather changes and {irrigation_readings} irrigation-zone changes in the last 28 days. "
                "Those state updates establish context, but do not yet measure rainfall, watering duration, or soil moisture."
            ),
            "next_step": "Capture precipitation forecasts and actual zone runtimes, restore or add soil-moisture data, then configure maximum-runtime safety rules before EcoNest makes recommendation-only irrigation suggestions.",
        }
    )
    insights.append(
        {
            "category": "Texas electricity cost",
            "state": "needs_setup",
            "confidence": 0,
            "headline": "Household electricity tariff is not configured.",
            "detail": "ERCOT market conditions alone are not the price on a residential bill. EcoNest needs the household's retail plan, including energy rate, delivery charges, tiers, and any time-of-use or free-period rules.",
            "next_step": "Add the retail electricity plan before EcoNest labels any period as cheaper or estimates dollars saved.",
        }
    )
    return {"insights": insights}


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


def _hour_label(hour: int) -> str:
    """Format a 24-hour value for a household-facing insight."""
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour} {suffix}"


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
