"""Tests for Home Assistant event-to-agent dispatching."""

import pytest

from orchestrator.config import Settings
from orchestrator.core.event_dispatcher import EventDispatcher


@pytest.mark.anyio
async def test_dispatches_motion_once_then_applies_cooldown() -> None:
    tasks = []

    async def submit(task):
        tasks.append(task)
        return "task-1"

    dispatcher = EventDispatcher(Settings(), submit)
    state = {"entity_id": "binary_sensor.hall_motion", "state": "on", "attributes": {"device_class": "motion"}}
    assert await dispatcher.dispatch([state]) == 1
    assert tasks[0].payload["type"] == "security"
    assert tasks[0].payload.get("action") is None
    assert await dispatcher.dispatch([state]) == 0
