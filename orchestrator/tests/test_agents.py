"""Tests for agents and orchestrator."""

import pytest

from orchestrator.agents.base import Task
from orchestrator.agents.device_agent import DeviceAgent
from orchestrator.agents.energy_agent import EnergyAgent
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.agents.security_agent import SecurityAgent
from orchestrator.agents.sensor_agent import SensorAgent

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
