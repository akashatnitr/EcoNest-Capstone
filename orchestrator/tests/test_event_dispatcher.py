"""Tests for normalized Home Assistant event-to-agent dispatching."""

import pytest

from orchestrator.agents.base import Task
from orchestrator.config import Settings
from orchestrator.core.event_dispatcher import EventDispatcher


@pytest.mark.anyio
async def test_dispatches_motion_once_then_applies_cooldown() -> None:
    tasks: list[Task] = []

    async def submit(task: Task) -> str:
        tasks.append(task)
        return "task-1"

    dispatcher = EventDispatcher(Settings(), submit)

    event = {
        "entity_id": "binary_sensor.hall_motion",
        "event_type": "motion_detected",
        "previous_state": "off",
        "new_state": "on",
        "metadata": {
            "friendly_name": "Hall Motion",
            "device_class": "motion",
        },
    }

    assert await dispatcher.dispatch([event]) == 1
    assert tasks[0].payload["type"] == "security"
    assert tasks[0].payload["event_type"] == "motion_detected"
    assert tasks[0].payload["motion"] is True

    assert await dispatcher.dispatch([event]) == 0


@pytest.mark.anyio
async def test_dispatches_appliance_cycle_to_energy_agent() -> None:
    tasks: list[Task] = []

    async def submit(task: Task) -> str:
        tasks.append(task)
        return "task-energy"

    dispatcher = EventDispatcher(Settings(), submit)

    event = {
        "entity_id": "sensor.washer_power",
        "event_type": "appliance_cycle_started",
        "previous_state": "20",
        "new_state": "600",
        "metadata": {
            "friendly_name": "Washer",
            "power_watts": 600,
        },
    }

    assert await dispatcher.dispatch([event]) == 1

    task = tasks[0]

    assert task.payload["type"] == "energy"
    assert task.payload["event_type"] == "appliance_cycle_started"
    assert task.payload["current_power_w"] == 600
    assert task.payload["device_name"] == "Washer"


@pytest.mark.anyio
async def test_dispatches_energy_anomaly_to_energy_agent() -> None:
    tasks: list[Task] = []

    async def submit(task: Task) -> str:
        tasks.append(task)
        return "task-energy-anomaly"

    dispatcher = EventDispatcher(Settings(), submit)

    event = {
        "entity_id": "sensor.washer_power",
        "event_type": "energy_anomaly_detected",
        "previous_state": "100",
        "new_state": "600",
        "metadata": {
            "friendly_name": "Washer",
            "power_watts": 600,
            "baseline_watts": 100,
            "anomaly_multiplier": 6.0,
        },
    }

    assert await dispatcher.dispatch([event]) == 1

    task = tasks[0]

    assert task.payload["type"] == "energy"
    assert task.payload["event_type"] == "energy_anomaly_detected"
    assert task.payload["entity_id"] == "sensor.washer_power"
    assert task.payload["current_power_w"] == 600
    assert task.payload["baseline_w"] == 100
    assert task.payload["device_name"] == "Washer"


@pytest.mark.anyio
async def test_ignores_unknown_normalized_event() -> None:
    tasks: list[Task] = []

    async def submit(task: Task) -> str:
        tasks.append(task)
        return "task-unknown"

    dispatcher = EventDispatcher(Settings(), submit)

    event = {
        "entity_id": "sensor.example",
        "event_type": "routine_temperature_update",
        "previous_state": "70",
        "new_state": "71",
        "metadata": {},
    }

    assert await dispatcher.dispatch([event]) == 0
    assert tasks == []
