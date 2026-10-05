from unittest.mock import AsyncMock

import pytest

from orchestrator.agents.base import Result, Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.core.event_dispatcher import EventDispatcher
from orchestrator.config import Settings


class FakeAgent:
    def __init__(self, name: str, handled_type: str):
        self.name = name
        self.handled_type = handled_type
        self.tasks = []

    async def can_handle(self, task: Task) -> bool:
        return task.payload.get("type") == self.handled_type

    async def execute(self, task: Task) -> Result:
        self.tasks.append(task)
        return Result(
            success=True,
            data={"received": task.payload},
            agent=self.name,
            task_id=task.id,
            message=f"{self.name} processed event",
        )


def make_settings() -> Settings:
    return Settings(
        HA_EVENT_DISPATCH_ENABLED=True,
        HA_EVENT_DISPATCH_COOLDOWN_SECONDS=60,
    )


@pytest.mark.asyncio
async def test_energy_event_reaches_energy_agent():
    energy_agent = FakeAgent("energy", "energy")
    security_agent = FakeAgent("security", "security")

    orchestrator = AgentOrchestrator(
        agents=[energy_agent, security_agent],
    )

    dispatcher = EventDispatcher(
        make_settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "sensor.dishwasher_power",
        "event_type": "energy_anomaly_detected",
        "previous_state": "120",
        "new_state": "650",
        "metadata": {
            "power_watts": 650.0,
            "baseline_watts": 100.0,
            "friendly_name": "Dishwasher Power",
        },
    }

    dispatched = await dispatcher.dispatch([event])

    assert dispatched == 1

    task_id = next(iter(orchestrator._tasks))
    task = await orchestrator._tasks[task_id]

    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "energy"

    assert len(energy_agent.tasks) == 1
    assert len(security_agent.tasks) == 0

    received = energy_agent.tasks[0]
    assert received.payload["type"] == "energy"
    assert received.payload["event_type"] == "energy_anomaly_detected"
    assert received.payload["use_llm"] is True
    assert received.payload["current_power_w"] == 650.0
    assert received.payload["baseline_w"] == 100.0


@pytest.mark.asyncio
async def test_motion_event_reaches_security_agent():
    energy_agent = FakeAgent("energy", "energy")
    security_agent = FakeAgent("security", "security")

    orchestrator = AgentOrchestrator(
        agents=[energy_agent, security_agent],
    )

    dispatcher = EventDispatcher(
        make_settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "binary_sensor.front_door_motion",
        "event_type": "motion_detected",
        "previous_state": "off",
        "new_state": "on",
        "metadata": {
            "friendly_name": "Front Door Motion",
        },
    }

    dispatched = await dispatcher.dispatch([event])

    assert dispatched == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]

    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "security"

    assert len(security_agent.tasks) == 1
    assert len(energy_agent.tasks) == 0

    received = security_agent.tasks[0]
    assert received.payload["type"] == "security"
    assert received.payload["event_type"] == "motion_detected"
    assert received.payload["use_llm"] is True
    assert received.payload["motion"] is True

@pytest.mark.asyncio
async def test_sensor_event_reaches_sensor_agent():
    sensor_agent = FakeAgent("sensor", "sensor")
    energy_agent = FakeAgent("energy", "energy")

    orchestrator = AgentOrchestrator(
        agents=[sensor_agent, energy_agent],
    )

    dispatcher = EventDispatcher(
        make_settings(),
        submit_task=orchestrator.submit,
    )

    event = {
        "entity_id": "sensor.wifi_soil_sensor",
        "event_type": "device_became_unavailable",
        "previous_state": "45",
        "new_state": "unavailable",
        "metadata": {
            "friendly_name": "WiFi Soil Sensor",
        },
    }

    dispatched = await dispatcher.dispatch([event])

    assert dispatched == 1

    task_id = next(iter(orchestrator._tasks))
    await orchestrator._tasks[task_id]

    result = await orchestrator.get_result(task_id)

    assert result is not None
    assert result.success is True
    assert result.agent == "sensor"
    assert len(sensor_agent.tasks) == 1
    assert len(energy_agent.tasks) == 0

    received = sensor_agent.tasks[0]
    assert received.payload["type"] == "sensor"
    assert received.payload["event_type"] == "device_became_unavailable"
    assert received.payload["use_llm"] is True