"""Durable, compact records of meaningful Home Assistant state transitions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HOME_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS home_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    occurred_at TIMESTAMP NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    device_id INT NULL,
    room_id INT NULL,
    event_type VARCHAR(64) NOT NULL,
    previous_state VARCHAR(64) NULL,
    new_state VARCHAR(64) NULL,
    metadata JSON NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'home_assistant',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_home_event (entity_id, occurred_at, event_type),
    INDEX idx_home_events_time (occurred_at),
    INDEX idx_home_events_type_time (event_type, occurred_at),
    INDEX idx_home_events_device_time (device_id, occurred_at),
    INDEX idx_home_events_room_time (room_id, occurred_at)
)
"""


async def ensure_home_events_schema(session: AsyncSession) -> None:
    """Create the durable event table when it does not already exist."""
    await session.execute(text(HOME_EVENTS_SCHEMA))
    await session.commit()


async def record_home_event(
    session: AsyncSession,
    event: dict[str, Any],
    *,
    device_id: int | None,
    room_id: int | None,
) -> None:
    """Persist one classified event without duplicating an observed transition."""
    await session.execute(
        text(
            """
            INSERT IGNORE INTO home_events (
                occurred_at, entity_id, device_id, room_id, event_type,
                previous_state, new_state, metadata, source
            ) VALUES (
                :occurred_at, :entity_id, :device_id, :room_id, :event_type,
                :previous_state, :new_state, :metadata, :source
            )
            """
        ),
        {
            **event,
            "device_id": device_id,
            "room_id": room_id,
            "metadata": json.dumps(event["metadata"], default=str),
        },
    )


async def purge_expired_home_events(session: AsyncSession, retention_days: int) -> int:
    """Delete only compact event records older than the configured retention period."""
    result = await session.execute(
        text(
            "DELETE FROM home_events "
            "WHERE occurred_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL :days DAY)"
        ),
        {"days": max(1, retention_days)},
    )
    return int(result.rowcount or 0)


def is_event_candidate(state: dict[str, Any]) -> bool:
    """Return whether a Home Assistant state can produce a meaningful event."""
    entity_id = str(state.get("entity_id") or "")
    domain, _, object_id = entity_id.partition(".")
    if domain in {"binary_sensor", "climate", "valve"}:
        return True
    if domain == "sensor" and _is_individual_power_sensor(state):
        return True
    if domain == "sensor" and _is_appliance_job_state_sensor(entity_id):
        return True
    return domain == "switch" and any(
        word in object_id.lower() for word in ("water", "sprinkler", "irrigation")
    )


def classify_home_event(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any] | None:
    """Classify one state transition, returning ``None`` for routine/noisy changes."""
    entity_id = str(current.get("entity_id") or "")
    if not entity_id or entity_id != str(previous.get("entity_id") or ""):
        return None

    previous_state = _state(previous)
    current_state = _state(current)
    attributes = _attributes(current)
    previous_attributes = _attributes(previous)
    event_type: str | None = None
    metadata: dict[str, Any] = {
        "friendly_name": attributes.get("friendly_name"),
        "device_class": attributes.get("device_class"),
    }

    if current_state == "unavailable" and previous_state != "unavailable":
        event_type = "device_became_unavailable"
    elif previous_state == "unavailable" and current_state != "unavailable":
        event_type = "device_restored"
    elif _is_watering_entity(entity_id):
        if previous_state == "off" and current_state == "on":
            event_type = "watering_started"
        elif previous_state == "on" and current_state == "off":
            event_type = "watering_completed"
    elif entity_id.startswith("binary_sensor."):
        device_class = str(attributes.get("device_class") or "").lower()
        if previous_state == "off" and current_state == "on":
            if device_class == "motion":
                event_type = "motion_detected"
            elif device_class in {"door", "garage_door", "opening", "window"}:
                event_type = f"{device_class}_opened"
        elif previous_state == "on" and current_state == "off" and device_class in {
            "door",
            "garage_door",
            "opening",
            "window",
        }:
            event_type = f"{device_class}_closed"
    elif entity_id.startswith("climate."):
        previous_target = previous_attributes.get("temperature")
        current_target = attributes.get("temperature")
        if current_target is not None and current_target != previous_target:
            event_type = "thermostat_target_changed"
            metadata["previous_target_temperature"] = previous_target
            metadata["target_temperature"] = current_target

    if event_type is None:
        return None
    return {
        "occurred_at": _occurred_at(current),
        "entity_id": entity_id,
        "event_type": event_type,
        "previous_state": previous_state or None,
        "new_state": current_state or None,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
        "source": "home_assistant",
    }


class PowerCycleTracker:
    """Turn sustained power draw from individual appliances into compact events."""

    def __init__(
        self,
        start_watts: float,
        end_watts: float,
        minimum_seconds: int,
    ) -> None:
        self.start_watts = max(1.0, start_watts)
        self.end_watts = max(0.0, min(end_watts, self.start_watts))
        self.minimum_seconds = max(1, minimum_seconds)
        self._candidates: dict[str, datetime] = {}
        self._started: dict[str, datetime] = {}

    def observe(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return cycle events caused by one individual-power transition."""
        if not _is_individual_power_sensor(current):
            return []
        entity_id = str(current.get("entity_id") or "")
        previous_watts = _power_watts(previous)
        current_watts = _power_watts(current)
        if not entity_id or previous_watts is None or current_watts is None:
            return []
        occurred_at = _occurred_at(current)
        candidate_at = self._candidates.get(entity_id)
        events: list[dict[str, Any]] = []

        if candidate_at is None and previous_watts < self.start_watts <= current_watts:
            self._candidates[entity_id] = occurred_at
            return []

        if candidate_at is not None and current_watts >= self.start_watts:
            if entity_id not in self._started and _duration_seconds(candidate_at, occurred_at) >= self.minimum_seconds:
                self._started[entity_id] = candidate_at
                events.append(
                    _power_cycle_event(
                        "appliance_cycle_started", current, candidate_at, occurred_at, 0
                    )
                )
            return events

        if candidate_at is not None and current_watts <= self.end_watts:
            duration_seconds = _duration_seconds(candidate_at, occurred_at)
            if duration_seconds >= self.minimum_seconds:
                if entity_id not in self._started:
                    events.append(
                        _power_cycle_event(
                            "appliance_cycle_started", current, candidate_at, candidate_at, 0
                        )
                    )
                events.append(
                    _power_cycle_event(
                        "appliance_cycle_completed",
                        current,
                        candidate_at,
                        occurred_at,
                        duration_seconds,
                    )
                )
            self._candidates.pop(entity_id, None)
            self._started.pop(entity_id, None)
        return events

class PowerAnomalyTracker:
    """Detect unusually high individual-appliance power draw."""

    def __init__(
        self,
        meaningful_watts: float = 100.0,
        anomaly_multiplier: float = 4.0,
        minimum_samples: int = 3,
        history_size: int = 12,
    ) -> None:
        self.meaningful_watts = max(1.0, meaningful_watts)
        self.anomaly_multiplier = max(1.0, anomaly_multiplier)
        self.minimum_samples = max(1, minimum_samples)
        self._history: dict[str, list[float]] = {}
        self._active: set[str] = set()
        self._history_size = max(2, history_size)

    def observe(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Detect an appliance spike without contaminating its baseline."""
        if not _is_individual_power_sensor(current):
            return []

        entity_id = str(current.get("entity_id") or "")
        current_watts = _power_watts(current)

        if not entity_id or current_watts is None:
            return []

        history = self._history.setdefault(entity_id, [])

        # A low reading means the appliance returned to normal.
        if current_watts < self.meaningful_watts:
            self._active.discard(entity_id)
            return []

        baseline_values = [
            value
            for value in history
            if value >= self.meaningful_watts
        ]

        # Establish a baseline before detecting anomalies.
        if len(baseline_values) < self.minimum_samples:
            history.append(current_watts)
            self._trim_history(history)
            return []

        baseline_watts = sum(baseline_values) / len(baseline_values)

        is_anomaly = (
            current_watts >= self.meaningful_watts
            and current_watts >= baseline_watts * self.anomaly_multiplier
        )

        if is_anomaly:
            # Don't repeatedly emit while the same spike remains active.
            if entity_id in self._active:
                return []

            self._active.add(entity_id)

            return [
                _power_anomaly_event(
                    current,
                    current_watts,
                    baseline_watts,
                )
            ]

        # Normal reading resets the anomaly state and becomes baseline data.
        self._active.discard(entity_id)
        history.append(current_watts)
        self._trim_history(history)

        return []

    def _trim_history(self, history: list[float]) -> None:
        if len(history) > self._history_size:
            del history[:-self._history_size]

def _power_anomaly_event(
    current: dict[str, Any],
    current_watts: float,
    baseline_watts: float,
) -> dict[str, Any]:
    """Build a normalized autonomous energy-anomaly event."""
    attributes = _attributes(current)

    return {
        "occurred_at": _occurred_at(current),
        "entity_id": str(current["entity_id"]),
        "event_type": "energy_anomaly_detected",
        "previous_state": _state(current) or None,
        "new_state": _state(current) or None,
        "metadata": {
            "friendly_name": attributes.get("friendly_name"),
            "power_watts": current_watts,
            "baseline_watts": baseline_watts,
            "anomaly_multiplier": (
                current_watts / baseline_watts
                if baseline_watts > 0
                else None
            ),
        },
        "source": "home_assistant",
    }

class ApplianceStatusCycleTracker:
    """Turn SmartThings-style appliance job states into full-cycle records."""

    def __init__(self) -> None:
        self._started: dict[str, datetime] = {}

    def observe(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return start/completion events for an appliance job-state transition."""
        entity_id = str(current.get("entity_id") or "")
        if not _is_appliance_job_state_sensor(entity_id):
            return []
        previous_state = _state(previous)
        current_state = _state(current)
        occurred_at = _occurred_at(current)
        events: list[dict[str, Any]] = []
        if (
            entity_id not in self._started
            and not _is_active_appliance_state(previous_state)
            and _is_active_appliance_state(current_state)
        ):
            self._started[entity_id] = occurred_at
            events.append(_status_cycle_event("appliance_cycle_started", current, occurred_at, 0))
            return events
        started_at = self._started.get(entity_id)
        if started_at is not None and _is_completed_appliance_state(current_state):
            events.append(
                _status_cycle_event(
                    "appliance_cycle_completed",
                    current,
                    started_at,
                    _duration_seconds(started_at, occurred_at),
                )
            )
            self._started.pop(entity_id, None)
        return events


def _is_watering_entity(entity_id: str) -> bool:
    domain, _, object_id = entity_id.partition(".")
    return domain in {"switch", "valve"} and any(
        word in object_id.lower() for word in ("water", "sprinkler", "irrigation")
    )


def _is_individual_power_sensor(state: dict[str, Any]) -> bool:
    """Exclude aggregate breaker/grid readings from appliance-cycle inference."""
    entity_id = str(state.get("entity_id") or "")
    if not entity_id.startswith("sensor."):
        return False
    attributes = _attributes(state)
    is_power = str(attributes.get("device_class") or "").lower() == "power"
    unit = str(attributes.get("unit_of_measurement") or "").lower()
    is_power = is_power or unit in {"w", "kw"}
    aggregate_words = ("breaker", "grid", "whole_home", "total", "input")
    return is_power and not any(word in entity_id.lower() for word in aggregate_words)


def _is_appliance_job_state_sensor(entity_id: str) -> bool:
    """Recognize the portable Home Assistant job-state naming convention."""
    return entity_id.startswith("sensor.") and entity_id.lower().endswith("_job_state")


def _is_active_appliance_state(state: str) -> bool:
    return state in {
        "washing",
        "drying",
        "cooling",
        "rinsing",
        "spinning",
        "running",
        "active",
        "operating",
        "paused",
    }


def _is_completed_appliance_state(state: str) -> bool:
    return state in {"finished", "complete", "completed", "done", "none", "off", "idle"}


def _power_watts(state: dict[str, Any]) -> float | None:
    """Normalize W and kW Home Assistant power readings to watts."""
    try:
        value = float(state.get("state"))
    except (TypeError, ValueError):
        return None
    unit = str(_attributes(state).get("unit_of_measurement") or "").lower()
    return value * 1_000 if unit == "kw" else value


def _duration_seconds(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds()))


def _power_cycle_event(
    event_type: str,
    current: dict[str, Any],
    started_at: datetime,
    occurred_at: datetime,
    duration_seconds: int,
) -> dict[str, Any]:
    attributes = _attributes(current)
    return {
        "occurred_at": occurred_at,
        "entity_id": str(current["entity_id"]),
        "event_type": event_type,
        "previous_state": None,
        "new_state": _state(current) or None,
        "metadata": {
            "friendly_name": attributes.get("friendly_name"),
            "cycle_started_at": started_at.isoformat(),
            "duration_seconds": duration_seconds,
            "power_watts": _power_watts(current),
        },
        "source": "home_assistant",
    }


def _status_cycle_event(
    event_type: str,
    current: dict[str, Any],
    started_at: datetime,
    duration_seconds: int,
) -> dict[str, Any]:
    """Build a durable event from a named appliance job-state transition."""
    attributes = _attributes(current)
    return {
        "occurred_at": _occurred_at(current) if event_type.endswith("completed") else started_at,
        "entity_id": str(current["entity_id"]),
        "event_type": event_type,
        "previous_state": None,
        "new_state": _state(current) or None,
        "metadata": {
            "friendly_name": attributes.get("friendly_name"),
            "cycle_started_at": started_at.isoformat(),
            "duration_seconds": duration_seconds,
            "job_state": _state(current),
        },
        "source": "home_assistant",
    }


def _attributes(state: dict[str, Any]) -> dict[str, Any]:
    attributes = state.get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def _state(state: dict[str, Any]) -> str:
    return str(state.get("state") or "").lower()


def _occurred_at(state: dict[str, Any]) -> datetime:
    """Normalize Home Assistant's state-change timestamp to a UTC database value."""
    value = state.get("last_changed") or state.get("last_updated")
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)).replace(
                tzinfo=None
            )
        except ValueError:
            pass
    return datetime.now(UTC).replace(tzinfo=None)
