"""Route normalized Home Assistant events to specialized agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.agents.base import Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.config import Settings
from orchestrator.core.audit import write_audit_event_async

SubmitTask = Callable[[Task], Awaitable[str]]


class EventDispatcher:
    """Route meaningful normalized HA events without performing device actions."""

    def __init__(
        self,
        settings: Settings,
        submit_task: SubmitTask | None = None,
    ) -> None:
        self.enabled = settings.HA_EVENT_DISPATCH_ENABLED
        self.cooldown = timedelta(
            seconds=max(30, settings.HA_EVENT_DISPATCH_COOLDOWN_SECONDS)
        )
        self._orchestrator = (
            AgentOrchestrator() if submit_task is None else None
        )
        self._submit_task = submit_task or self._orchestrator.submit
        self._last_dispatched: dict[str, datetime] = {}

    async def dispatch(self, events: list[dict[str, Any]]) -> int:
        """Route normalized Home Assistant events to the agent system."""
        if not self.enabled:
            return 0

        count = 0

        for event in events:
            normalized = _normalize_event(event)

            if normalized is None:
                continue

            if not self._reserve(normalized["key"]):
                continue

            task = Task(
                intent=normalized["intent"],
                payload=normalized["payload"],
                metadata={
                    "source": "ha_event_dispatcher",
                    "event_type": normalized["event_type"],
                },
            )

            task_id = await self._submit_task(task)
            count += 1

            await write_audit_event_async(
                "ha.event.dispatched",
                {
                    "task_id": task_id,
                    **event,
                },
            )

        return count

    def _reserve(self, key: str) -> bool:
        """Apply the per-event cooldown."""
        now = datetime.now(UTC)
        previous = self._last_dispatched.get(key)

        if previous is not None and now - previous < self.cooldown:
            return False

        self._last_dispatched[key] = now
        return True


def _normalize_event(
    event: dict[str, Any],
) -> dict[str, Any] | None:
    """Convert a normalized HA event into the existing Task contract."""
    entity_id = str(event.get("entity_id") or "")
    event_type = str(event.get("event_type") or "")

    if not entity_id or not event_type:
        return None

    category = _event_category(event_type)

    if category is None:
        return None

    previous_state = event.get("previous_state")
    new_state = event.get("new_state")
    metadata = (
        event.get("metadata")
        if isinstance(event.get("metadata"), dict)
        else {}
    )

    payload: dict[str, Any] = {
        "type": category,
        "event_type": event_type,
        "entity_id": entity_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "metadata": metadata,
        "trigger": "ha_event_dispatcher",
    }

    # Preserve the input contracts expected by the specialist agents.
    if category == "security":
        if event_type == "motion_detected":
            payload["motion"] = True

    if category == "energy":
        power_watts = metadata.get("power_watts")
        if power_watts is not None:
            payload["current_power_w"] = power_watts

        baseline_watts = metadata.get("baseline_watts")
        if baseline_watts is not None:
            payload["baseline_w"] = baseline_watts

        friendly_name = metadata.get("friendly_name")
        if friendly_name:
            payload["device_name"] = friendly_name

    return {
        "key": f"{event_type}:{entity_id}",
        "category": category,
        "event_type": event_type,
        "entity_id": entity_id,
        "intent": _event_intent(category, event_type, entity_id),
        "payload": payload,
    }


def _event_category(event_type: str) -> str | None:
    """Map normalized home events to existing specialist agents."""
    security_events = {
        "motion_detected",
        "door_opened",
        "door_closed",
        "garage_door_opened",
        "garage_door_closed",
        "opening_opened",
        "opening_closed",
        "window_opened",
        "window_closed",
    }

    irrigation_events = {
        "watering_started",
        "watering_completed",
    }

    sensor_events = {
        "device_became_unavailable",
        "device_restored",
    }

    energy_events = {
        "appliance_cycle_started",
        "appliance_cycle_completed",
        "energy_anomaly_detected",
    }

    if event_type in security_events:
        return "security"

    if event_type in irrigation_events:
        return "irrigation"

    if event_type in sensor_events:
        return "sensor"

    if event_type in energy_events:
        return "energy"

    if event_type == "thermostat_target_changed":
        return "device"

    return None


def _event_intent(
    category: str,
    event_type: str,
    entity_id: str,
) -> str:
    """Build a deterministic intent for specialist-agent routing."""
    return f"{category} event: {event_type} at {entity_id}"
