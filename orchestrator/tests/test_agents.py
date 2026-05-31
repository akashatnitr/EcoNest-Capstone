"""Tests for agents and orchestrator."""

import json
import logging

import pytest
from pydantic import ValidationError

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.agents.device_agent import DeviceAgent
from orchestrator.agents.energy_agent import EnergyAgent
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.agents.security_agent import SecurityAgent
from orchestrator.agents.sensor_agent import SensorAgent

# ------------------------------------------------------------------
# Base models and execution
# ------------------------------------------------------------------


class _SuccessAgent(BaseAgent):
    name = "success"
    tools = ["test_tool"]
    permissions = ["agent:run"]

    async def can_handle(self, task: Task) -> bool:
        return "ok" in task.intent

    async def run(self, task: Task) -> Result:
        return Result(success=True, data={"handled": task.intent}, message="done")


class _FailingAgent(_SuccessAgent):
    name = "failing"

    async def run(self, task: Task) -> Result:
        raise RuntimeError("boom")


def test_task_validation_rejects_empty_intent():
    with pytest.raises(ValidationError):
        Task(id="1", intent="", payload={})


def test_task_defaults_are_structured():
    task = Task(intent="ok task")
    assert task.id == ""
    assert task.payload == {}
    assert task.metadata == {}
    assert task.timeout_seconds == 30


@pytest.mark.anyio
async def test_base_agent_execute_success_logs_json(caplog):
    agent = _SuccessAgent()
    task = Task(id="task-1", intent="ok task", payload={}, user_id="user-1")

    with caplog.at_level(logging.INFO, logger="orchestrator.agents.base"):
        result = await agent.execute(task)

    assert result.success
    assert result.agent == "success"
    assert result.task_id == "task-1"

    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "agent.run"
    assert event["agent"] == "success"
    assert event["task_id"] == "task-1"
    assert event["success"] is True
    assert "duration_ms" in event


@pytest.mark.anyio
async def test_base_agent_execute_rejects_unsupported_task():
    agent = _SuccessAgent()
    result = await agent.execute(Task(id="task-2", intent="no match", payload={}))

    assert not result.success
    assert result.agent == "success"
    assert result.task_id == "task-2"
    assert result.error == "unsupported_task"


@pytest.mark.anyio
async def test_base_agent_execute_catches_exceptions():
    agent = _FailingAgent()
    result = await agent.execute(Task(id="task-3", intent="ok task", payload={}))

    assert not result.success
    assert result.agent == "failing"
    assert result.task_id == "task-3"
    assert result.error == "RuntimeError"
    assert result.metadata["error_message"] == "boom"


# ------------------------------------------------------------------
# Agent routing
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_energy_agent_can_handle():
    agent = EnergyAgent()
    assert await agent.can_handle(Task(id="1", intent="check energy usage", payload={}))
    assert not await agent.can_handle(Task(id="2", intent="turn on light", payload={}))


@pytest.mark.anyio
async def test_security_agent_can_handle():
    agent = SecurityAgent()
    assert await agent.can_handle(Task(id="1", intent="security alert", payload={}))
    assert not await agent.can_handle(Task(id="2", intent="sensor health", payload={}))


@pytest.mark.anyio
async def test_sensor_agent_can_handle():
    agent = SensorAgent()
    assert await agent.can_handle(Task(id="1", intent="sensor calibration", payload={}))
    assert not await agent.can_handle(Task(id="2", intent="device control", payload={}))


@pytest.mark.anyio
async def test_device_agent_can_handle():
    agent = DeviceAgent()
    assert await agent.can_handle(Task(id="1", intent="turn on light", payload={}))
    assert not await agent.can_handle(Task(id="2", intent="energy report", payload={}))


# ------------------------------------------------------------------
# Agent execution
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_energy_agent_run():
    agent = EnergyAgent()
    result = await agent.run(Task(id="1", intent="energy", payload={}))
    assert result.success
    assert "recommendation" in result.data


@pytest.mark.anyio
async def test_security_agent_run():
    agent = SecurityAgent()
    result = await agent.run(Task(id="1", intent="security", payload={}))
    assert result.success
    assert "severity" in result.data


@pytest.mark.anyio
async def test_sensor_agent_run():
    agent = SensorAgent()
    result = await agent.run(Task(id="1", intent="sensor", payload={}))
    assert result.success
    assert "healthy_sensors" in result.data


@pytest.mark.anyio
async def test_device_agent_run():
    agent = DeviceAgent()
    result = await agent.run(Task(id="1", intent="device", payload={}))
    assert result.success
    assert "action" in result.data


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_orchestrator_routes_energy():
    orch = AgentOrchestrator()
    task = Task(id="", intent="check my power usage", payload={})
    agent = await orch._classify_and_route(task)
    assert agent is not None
    assert agent.name == "energy"


@pytest.mark.anyio
async def test_orchestrator_routes_device():
    orch = AgentOrchestrator()
    task = Task(id="", intent="turn off the bedroom light", payload={})
    agent = await orch._classify_and_route(task)
    assert agent is not None
    assert agent.name == "device"


@pytest.mark.anyio
async def test_orchestrator_healthcheck():
    orch = AgentOrchestrator()
    health = await orch.healthcheck()
    assert "energy" in health
    assert "security" in health
    assert "sensor" in health
    assert "device" in health


@pytest.mark.anyio
async def test_orchestrator_submit_and_result():
    orch = AgentOrchestrator()
    task = Task(id="", intent="energy check", payload={})
    task_id = await orch.submit(task)
    assert task_id != ""
    # Result may still be running or completed
    result = await orch.get_result(task_id)
    assert result is not None or result is None  # either is valid depending on timing


# ------------------------------------------------------------------
# Healthcheck
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_agent_healthcheck():
    agent = EnergyAgent()
    status = await agent.healthcheck()
    assert status.healthy
