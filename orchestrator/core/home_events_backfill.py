"""Backfill compact home events from retained raw Home Assistant readings."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from orchestrator.config import Settings
from orchestrator.core.database import mysql_session_context
from orchestrator.core.home_events import (
    ApplianceStatusCycleTracker,
    PowerCycleTracker,
    classify_home_event,
    is_event_candidate,
    record_home_event,
)


class HomeEventsBackfillSummary(BaseModel):
    """Outcome of one idempotent retained-reading event reconstruction run."""

    readings_scanned: int = Field(default=0, ge=0)
    candidate_readings: int = Field(default=0, ge=0)
    event_records_attempted: int = Field(default=0, ge=0)
    batches: int = Field(default=0, ge=0)


async def backfill_home_events(
    settings: Settings,
    *,
    batch_size: int = 5_000,
    dry_run: bool = False,
) -> HomeEventsBackfillSummary:
    """Derive durable events from all retained raw states in timestamp order.

    The source readings are never changed. Re-running the operation is safe because
    ``home_events`` de-duplicates records by entity, event type, and occurrence time.
    """
    summary = HomeEventsBackfillSummary()
    previous_states: dict[str, dict[str, Any]] = {}
    power_cycles = PowerCycleTracker(
        settings.APPLIANCE_CYCLE_START_WATTS,
        settings.APPLIANCE_CYCLE_END_WATTS,
        settings.APPLIANCE_CYCLE_MINIMUM_SECONDS,
    )
    appliance_status_cycles = ApplianceStatusCycleTracker()
    cursor_timestamp: datetime | None = None
    cursor_id = 0
    limit = max(100, min(batch_size, 20_000))

    while True:
        async with mysql_session_context() as session:
            rows = await _read_batch(session, cursor_timestamp, cursor_id, limit)
            if not rows:
                break
            for row in rows:
                summary.readings_scanned += 1
                timestamp = row.get("timestamp")
                if isinstance(timestamp, datetime):
                    cursor_timestamp = timestamp
                    cursor_id = int(row["reading_id"])

                state = _state_from_reading(row)
                if state is None or not is_event_candidate(state):
                    continue
                summary.candidate_readings += 1
                entity_id = str(state["entity_id"])
                previous = previous_states.get(entity_id)
                if previous is not None and _state_revision(previous) != _state_revision(state):
                    events = [event for event in [classify_home_event(previous, state)] if event]
                    events.extend(power_cycles.observe(previous, state))
                    events.extend(appliance_status_cycles.observe(previous, state))
                    for event in events:
                        event["source"] = "historical_backfill"
                        summary.event_records_attempted += 1
                        if not dry_run:
                            await record_home_event(
                                session,
                                event,
                                device_id=int(row["device_id"]),
                                room_id=int(row["room_id"]),
                            )
                previous_states[entity_id] = state

            if not dry_run:
                await session.commit()
            summary.batches += 1
            if len(rows) < limit:
                break
    return summary


async def _read_batch(
    session: Any,
    cursor_timestamp: datetime | None,
    cursor_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Read one timestamp-ordered raw-reading batch with enough device context."""
    if cursor_timestamp is None:
        condition = "sr.timestamp IS NOT NULL"
        params: dict[str, Any] = {"limit": limit}
    else:
        condition = "(sr.timestamp > :cursor_timestamp OR (sr.timestamp = :cursor_timestamp AND sr.id > :cursor_id))"
        params = {"cursor_timestamp": cursor_timestamp, "cursor_id": cursor_id, "limit": limit}
    result = await session.execute(
        text(
            "SELECT sr.id AS reading_id, sr.timestamp, sr.data, d.id AS device_id, d.room_id, "
            "d.ha_entity_id FROM sensor_readings sr "
            "JOIN devices d ON d.id = sr.device_id "
            f"WHERE {condition} "
            "AND (d.ha_entity_id LIKE 'binary_sensor.%' OR d.ha_entity_id LIKE 'climate.%' "
            "OR d.ha_entity_id LIKE 'sensor.%' OR d.ha_entity_id LIKE 'switch.%' "
            "OR d.ha_entity_id LIKE 'valve.%') "
            "ORDER BY sr.timestamp ASC, sr.id ASC LIMIT :limit"
        ),
        params,
    )
    return [dict(row) for row in result.mappings().all()]


def _state_from_reading(row: dict[str, Any]) -> dict[str, Any] | None:
    """Restore a Home Assistant-style state from one stored JSON reading."""
    entity_id = row.get("ha_entity_id")
    if not entity_id:
        return None
    data = row.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    timestamp = row.get("timestamp")
    fallback_time = timestamp.isoformat() if isinstance(timestamp, datetime) else None
    return {
        "entity_id": str(entity_id),
        "state": data.get("state"),
        "attributes": data.get("attributes") if isinstance(data.get("attributes"), dict) else {},
        "last_changed": data.get("last_changed") or fallback_time,
        "last_updated": data.get("last_updated") or fallback_time,
    }


def _state_revision(state: dict[str, Any]) -> str:
    """Match normal ingestion's cheap state-transition detection rule."""
    return str(state.get("last_updated") or state.get("last_changed") or state.get("state"))
