"""Evidence-backed household behavior profiling from retained EcoNest data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BEHAVIOR_PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS behavior_profiles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    profile_key VARCHAR(100) NOT NULL,
    profile JSON NOT NULL,
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_behavior_profile (profile_key),
    INDEX idx_behavior_profile_generated (generated_at)
)
"""


async def ensure_behavior_profile_schema(session: AsyncSession) -> None:
    """Create the household profile store for existing and fresh deployments."""
    await session.execute(text(BEHAVIOR_PROFILE_SCHEMA))
    await session.commit()


async def build_behavior_profile(session: AsyncSession) -> dict[str, Any]:
    """Calculate, label, and persist only patterns supported by retained records."""
    comfort_rows = await _mappings(
        session,
        """
        SELECT r.name AS room, co.climate_entity_id, COUNT(*) AS observations,
               COUNT(DISTINCT DATE(co.observed_at)) AS observed_days,
               SUM(co.target_changed = TRUE) AS target_changes,
               ROUND(AVG(co.target_temperature), 1) AS average_target_f,
               MIN(co.target_temperature) AS min_target_f, MAX(co.target_temperature) AS max_target_f,
               ROUND(AVG(co.current_temperature), 1) AS average_current_f,
               ROUND(AVG(co.humidity_percent), 1) AS average_humidity_percent,
               MIN(co.observed_at) AS first_observed, MAX(co.observed_at) AS last_observed
        FROM comfort_observations co JOIN rooms r ON r.id = co.room_id
        WHERE co.target_temperature IS NOT NULL
        GROUP BY r.name, co.climate_entity_id
        ORDER BY observations DESC
        """,
    )
    cycle_rows = await _mappings(
        session,
        """
        SELECT COALESCE(d.name, he.entity_id) AS appliance, he.entity_id,
               COUNT(*) AS completed_cycles,
               ROUND(AVG(CAST(JSON_UNQUOTE(JSON_EXTRACT(he.metadata, '$.duration_seconds')) AS UNSIGNED)) / 60, 1) AS average_minutes,
               MIN(he.occurred_at) AS first_cycle, MAX(he.occurred_at) AS last_cycle
        FROM home_events he LEFT JOIN devices d ON d.id = he.device_id
        WHERE he.event_type = 'appliance_cycle_completed'
          AND CAST(JSON_UNQUOTE(JSON_EXTRACT(he.metadata, '$.duration_seconds')) AS UNSIGNED) <= 86400
        GROUP BY d.name, he.entity_id
        ORDER BY completed_cycles DESC
        """,
    )
    excluded_cycle_rows = await _mappings(
        session,
        """
        SELECT COALESCE(d.name, he.entity_id) AS appliance, COUNT(*) AS excluded_records
        FROM home_events he LEFT JOIN devices d ON d.id = he.device_id
        WHERE he.event_type = 'appliance_cycle_completed'
          AND CAST(JSON_UNQUOTE(JSON_EXTRACT(he.metadata, '$.duration_seconds')) AS UNSIGNED) > 86400
        GROUP BY d.name, he.entity_id
        ORDER BY excluded_records DESC
        """,
    )
    cycle_time_rows = await _mappings(
        session,
        """
        SELECT he.entity_id, HOUR(he.occurred_at) AS hour_of_day,
               DAYNAME(he.occurred_at) AS weekday, COUNT(*) AS occurrences
        FROM home_events he
        WHERE he.event_type = 'appliance_cycle_completed'
        GROUP BY he.entity_id, HOUR(he.occurred_at), DAYNAME(he.occurred_at)
        ORDER BY occurrences DESC
        """,
    )
    motion_rows = await _mappings(
        session,
        """
        SELECT COALESCE(r.name, he.entity_id) AS area, HOUR(he.occurred_at) AS hour_of_day,
               DAYNAME(he.occurred_at) AS weekday, COUNT(*) AS motion_events, MIN(he.occurred_at) AS first_observed,
               MAX(he.occurred_at) AS last_observed
        FROM home_events he LEFT JOIN rooms r ON r.id = he.room_id
        WHERE he.event_type = 'motion_detected'
        GROUP BY r.name, he.entity_id, HOUR(he.occurred_at), DAYNAME(he.occurred_at)
        ORDER BY motion_events DESC
        LIMIT 12
        """,
    )
    health_rows = await _mappings(
        session,
        """
        SELECT he.entity_id, COUNT(*) AS outages, MAX(he.occurred_at) AS last_unavailable_at
        FROM home_events he WHERE he.event_type = 'device_became_unavailable'
        GROUP BY he.entity_id ORDER BY outages DESC LIMIT 12
        """,
    )
    coverage_rows = await _mappings(
        session,
        """
        SELECT (SELECT MIN(timestamp) FROM sensor_readings) AS raw_start,
               (SELECT MAX(timestamp) FROM sensor_readings) AS raw_end,
               (SELECT MIN(hour_start) FROM energy_hourly_analytics) AS energy_start,
               (SELECT MAX(hour_start) FROM energy_hourly_analytics) AS energy_end,
               (SELECT COUNT(*) FROM home_events) AS event_count,
               (SELECT COUNT(*) FROM irrigation_runs) AS irrigation_runs
        """,
    )
    cycle_times = _top_cycle_times(cycle_time_rows)
    profile = {
        "generated_at": datetime.now(UTC).isoformat(),
        "coverage": coverage_rows[0] if coverage_rows else {},
        "comfort": [_comfort_profile(row) for row in _latest_comfort_per_climate(comfort_rows)],
        "appliance_routines": [_appliance_profile(row, cycle_times.get(str(row["entity_id"]))) for row in cycle_rows],
        "motion_patterns": [_motion_profile(row) for row in motion_rows],
        "device_health": health_rows,
        "data_quality_notes": _data_quality_notes(excluded_cycle_rows),
        "unavailable_capabilities": [
            "Home/away routines are not inferred without an explicit, consented presence signal.",
            "TV, computer, or appliance routines appear only when Home Assistant exposes a reliable state or individual power/job-state history.",
            "Inferred patterns remain advisory until a user confirms a preference or automation."
        ],
    }
    await session.execute(
        text(
            "INSERT INTO behavior_profiles (profile_key, profile, generated_at) "
            "VALUES ('household_behavior', :profile, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE profile = VALUES(profile), generated_at = VALUES(generated_at)"
        ),
        {"profile": json.dumps(profile, default=str)},
    )
    await session.commit()
    return profile


async def _mappings(session: AsyncSession, sql: str) -> list[dict[str, Any]]:
    result = await session.execute(text(sql))
    return [dict(row) for row in result.mappings().all()]


def _top_cycle_times(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    top: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row["entity_id"])
        if entity_id not in top:
            top[entity_id] = row
    return top


def _comfort_profile(row: dict[str, Any]) -> dict[str, Any]:
    observations = int(row["observations"])
    target_range = float(row["max_target_f"]) - float(row["min_target_f"])
    return {
        **row,
        "confidence": _confidence(observations),
        "interpretation": (
            "consistent observed thermostat target — confirmation needed"
            if int(row["observed_days"]) >= 7 and target_range <= 2
            else "observed thermostat target — confirmation needed"
        ),
    }


def _appliance_profile(row: dict[str, Any], typical_time: dict[str, Any] | None) -> dict[str, Any]:
    result = {**row, "confidence": _confidence(int(row["completed_cycles"]))}
    if typical_time is not None:
        result["most_observed_time"] = {"weekday": typical_time["weekday"], "hour_of_day": typical_time["hour_of_day"], "occurrences": typical_time["occurrences"]}
    return result


def _motion_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Label motion conservatively: it cannot establish occupancy on its own."""
    return {
        **row,
        "confidence": _confidence(int(row["motion_events"])),
        "note": "Motion is activity evidence, not proof that someone is home.",
    }


def _latest_comfort_per_climate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer the most recently observed room mapping for each thermostat."""
    newest: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row["climate_entity_id"])
        previous = newest.get(entity_id)
        if previous is None or row["last_observed"] > previous["last_observed"]:
            newest[entity_id] = row
    return list(newest.values())


def _data_quality_notes(excluded_cycle_rows: list[dict[str, Any]]) -> list[str]:
    """Explain records deliberately excluded from routine inference."""
    notes = [
        "Completed power cycles lasting longer than 24 hours are excluded from routine estimates because they may represent a sensor gap or standby state rather than active use."
    ]
    if excluded_cycle_rows:
        details = ", ".join(
            f"{row['appliance']} ({row['excluded_records']})" for row in excluded_cycle_rows
        )
        notes.append(f"Excluded unusually long cycle record(s): {details}.")
    return notes


def _confidence(samples: int) -> str:
    if samples >= 30:
        return "high"
    if samples >= 10:
        return "medium"
    return "low"
