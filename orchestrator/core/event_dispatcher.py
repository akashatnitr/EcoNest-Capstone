"""Safe routing of meaningful Home Assistant state changes to agents."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from orchestrator.agents.base import Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.config import Settings
from orchestrator.core.audit import write_audit_event_async

logger = logging.getLogger(__name__)
SubmitTask = Callable[[Task], Awaitable[str]]


class EventDispatcher:
    """Filter HA changes into analysis-only agent tasks with cooldowns."""

    def __init__(self, settings: Settings, submit_task: SubmitTask | None = None) -> None:
        self.enabled = settings.HA_EVENT_DISPATCH_ENABLED
        self.cooldown = timedelta(seconds=max(30, settings.HA_EVENT_DISPATCH_COOLDOWN_SECONDS))
        self._orchestrator = AgentOrchestrator() if submit_task is None else None
        self._submit_task = submit_task or self._orchestrator.submit
        self._last_dispatched: dict[str, datetime] = {}

    async def dispatch(self, states: list[dict[str, Any]]) -> int:
        """Submit only actionable observations; never include device actions."""
        if not self.enabled:
            return 0
        count = 0
        for state in states:
            event = _event_for_state(state)
            if event is None or not self._reserve(event["key"]):
                continue
            task = Task(
                intent=event["intent"],
                payload={"type": event["category"], "entity_id": event["entity_id"], "state": event["state"], "trigger": "ha_event_dispatcher"},
                metadata={"source": "ha_event_dispatcher", "event_type": "state_changed"},
            )
            task_id = await self._submit_task(task)
            count += 1
            await write_audit_event_async("ha.event.dispatched", {"task_id": task_id, **event})
        return count

    def _reserve(self, key: str) -> bool:
        now = datetime.now(UTC)
        previous = self._last_dispatched.get(key)
        if previous is not None and now - previous < self.cooldown:
            return False
        self._last_dispatched[key] = now
        return True


def _event_for_state(state: dict[str, Any]) -> dict[str, str] | None:
    entity_id = str(state.get("entity_id", ""))
    value = str(state.get("state", "")).lower()
    attributes = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
    device_class = str(attributes.get("device_class", "")).lower()
    if entity_id.startswith("binary_sensor.") and value == "on" and device_class in {"motion", "door", "window", "opening"}:
        return {"key": f"security:{entity_id}", "category": "security", "entity_id": entity_id, "state": value, "intent": f"Security event: {device_class or 'binary sensor'} detected at {entity_id}"}
    if entity_id.startswith(("sensor.", "binary_sensor.")) and value in {"unavailable", "unknown"}:
        return {"key": f"sensor:{entity_id}", "category": "sensor", "entity_id": entity_id, "state": value, "intent": f"Sensor health event: {entity_id} is {value}"}
    return None
