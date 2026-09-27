"""Historical irrigation-run import and weather evidence for analytics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings
from orchestrator.core.weather import HISTORICAL_WEATHER_SOURCE

IRRIGATION_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS irrigation_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_id VARCHAR(255) NOT NULL,
    zone_name VARCHAR(255) NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP NOT NULL,
    duration_seconds INT NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'home_assistant_history',
    weather_condition VARCHAR(64) NULL,
    outdoor_temperature_f FLOAT NULL,
    precipitation_in FLOAT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_irrigation_run (entity_id, started_at),
    INDEX idx_irrigation_run_time (started_at),
    INDEX idx_irrigation_run_entity_time (entity_id, started_at)
)
"""


async def ensure_irrigation_runs_schema(session: AsyncSession) -> None:
    """Create the retained irrigation-run evidence table when needed."""
    await session.execute(text(IRRIGATION_RUNS_SCHEMA))
    await session.commit()


async def backfill_home_assistant_irrigation_runs(
    session: AsyncSession,
    settings: Settings,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Import completed manual-watering runs from Home Assistant history."""
    if not settings.HA_TOKEN:
        raise RuntimeError("HA_TOKEN is required to import irrigation history")
    if start >= end:
        raise ValueError("start must be before end")
    await ensure_irrigation_runs_schema(session)
    entities = await _manual_watering_entities(session)
    if not entities:
        return {"entities": 0, "runs_upserted": 0}
    headers = {"Authorization": f"Bearer {settings.HA_TOKEN}"}
    params = {
        "filter_entity_id": ",".join(entities),
        "end_time": _as_utc(end).isoformat(),
        "no_attributes": "",
    }
    url = f"{settings.HA_URL.rstrip('/')}/api/history/period/{_as_utc(start).isoformat()}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
    runs = _completed_runs(response.json(), entities)
    if runs:
        await session.execute(
            text(
                """
                INSERT INTO irrigation_runs (
                    entity_id, zone_name, started_at, ended_at, duration_seconds, source
                ) VALUES (
                    :entity_id, :zone_name, :started_at, :ended_at, :duration_seconds, :source
                )
                ON DUPLICATE KEY UPDATE
                    ended_at = VALUES(ended_at), duration_seconds = VALUES(duration_seconds),
                    source = VALUES(source)
                """
            ),
            runs,
        )
        await _attach_historical_weather(session)
    await session.commit()
    return {"entities": len(entities), "runs_upserted": len(runs)}


async def _manual_watering_entities(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text(
            """
            SELECT ha_entity_id FROM devices
            WHERE ha_entity_id LIKE 'switch.%_manual_watering'
            ORDER BY ha_entity_id
            """
        )
    )
    return [str(row[0]) for row in result.all() if row[0]]


def _completed_runs(payload: Any, entities: list[str]) -> list[dict[str, Any]]:
    """Pair retained on/off state transitions into completed watering runs."""
    histories = payload if isinstance(payload, list) else []
    events_by_entity: dict[str, list[dict[str, Any]]] = {entity: [] for entity in entities}
    for history in histories:
        if not isinstance(history, list):
            continue
        for event in history:
            if not isinstance(event, dict):
                continue
            entity_id = str(event.get("entity_id") or "")
            if entity_id in events_by_entity:
                events_by_entity[entity_id].append(event)

    runs: list[dict[str, Any]] = []
    for entity_id, events in events_by_entity.items():
        active_at: datetime | None = None
        for event in sorted(events, key=lambda item: _event_time(item) or datetime.min.replace(tzinfo=UTC)):
            observed_at = _event_time(event)
            if observed_at is None:
                continue
            state = str(event.get("state") or "").lower()
            if state == "on" and active_at is None:
                active_at = observed_at
            elif state == "off" and active_at is not None and observed_at > active_at:
                duration = int((observed_at - active_at).total_seconds())
                if duration <= 24 * 60 * 60:
                    runs.append(
                        {
                            "entity_id": entity_id,
                            "zone_name": _zone_name(entity_id),
                            "started_at": active_at.replace(tzinfo=None),
                            "ended_at": observed_at.replace(tzinfo=None),
                            "duration_seconds": duration,
                            "source": "home_assistant_history",
                        }
                    )
                active_at = None
    return runs


async def _attach_historical_weather(session: AsyncSession) -> None:
    """Add archived outdoor conditions nearest to each completed irrigation run."""
    await session.execute(
        text(
            """
            UPDATE irrigation_runs ir
            JOIN weather_forecasts wf
              ON wf.source_entity_id = :source
             AND wf.forecast_at = DATE_FORMAT(ir.started_at, '%Y-%m-%d %H:00:00')
            SET ir.weather_condition = wf.condition_name,
                ir.outdoor_temperature_f = wf.temperature_f,
                ir.precipitation_in = wf.precipitation_in
            WHERE ir.source = 'home_assistant_history'
            """
        ),
        {"source": HISTORICAL_WEATHER_SOURCE},
    )


def _event_time(event: dict[str, Any]) -> datetime | None:
    value = event.get("last_changed") or event.get("last_updated")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _zone_name(entity_id: str) -> str:
    return entity_id.removeprefix("switch.").removesuffix("_manual_watering").replace("_", " ").title()


def _as_utc(value: datetime) -> datetime:
    """Normalize API boundary timestamps to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
