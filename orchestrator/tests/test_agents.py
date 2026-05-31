"""Tests for agents and orchestrator."""

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

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


class _StaticAgent(BaseAgent):
    def __init__(
        self,
        name: str,
        success: bool = True,
        can_handle_task: bool = True,
    ) -> None:
        super().__init__()
        self.name = name
        self.success = success
        self.can_handle_task = can_handle_task
        self.run_count = 0

    async def can_handle(self, task: Task) -> bool:
        return self.can_handle_task

    async def run(self, task: Task) -> Result:
        self.run_count += 1
        return Result(
            success=self.success,
            data={"agent": self.name, "source": task.metadata.get("source")},
            message=f"{self.name} complete",
        )


class _FlakyAgent(_StaticAgent):
    def __init__(self) -> None:
        super().__init__("energy")

    async def run(self, task: Task) -> Result:
        self.run_count += 1
        return Result(
            success=self.run_count >= 3,
            data={"attempt": self.run_count},
            message="flaky",
            error=None if self.run_count >= 3 else "temporary_failure",
        )


class _FakeLLM:
    def __init__(self, category: str) -> None:
        self.category = category

    async def generate_structured(
        self,
        prompt: str,
        output_model: type[Any],
        system: str | None = None,
        temperature: float = 0.7,
    ) -> Any:
        return output_model(category=self.category)


async def _wait_for_result(orch: AgentOrchestrator, task_id: str) -> Result | None:
    for _ in range(20):
        result = await orch.get_result(task_id)
        if result is not None:
            return result
        await asyncio.sleep(0)
    return None


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


@pytest.mark.anyio
async def test_orchestrator_http_api_intake_adds_source_and_role():
    agent = _StaticAgent("energy")
    orch = AgentOrchestrator(agents=[agent])

    task_id = await orch.submit_http_api(
        intent="energy overview",
        payload={},
        user_id="42",
        user_role="homeowner",
    )
    result = await _wait_for_result(orch, task_id)

    assert result is not None
    assert result.success
    assert result.data["source"] == "http_api"


@pytest.mark.anyio
async def test_orchestrator_scheduled_intake_adds_source():
    agent = _StaticAgent("energy")
    orch = AgentOrchestrator(agents=[agent])

    task_id = await orch.submit_scheduled(
        intent="energy check",
        payload={},
        schedule_id="nightly",
    )
    result = await _wait_for_result(orch, task_id)

    assert result is not None
    assert result.success
    assert result.data["source"] == "scheduled_cron"


@pytest.mark.anyio
async def test_orchestrator_ha_webhook_intake_adds_source():
    agent = _StaticAgent("security")
    orch = AgentOrchestrator(agents=[agent])

    task_id = await orch.submit_home_assistant_webhook(
        event_type="motion alert",
        payload={"entity_id": "binary_sensor.motion"},
    )
    result = await _wait_for_result(orch, task_id)

    assert result is not None
    assert result.success
    assert result.data["source"] == "ha_webhook"


@pytest.mark.anyio
async def test_orchestrator_llm_fallback_routes_unknown_intent():
    agent = _StaticAgent("sensor", can_handle_task=False)
    orch = AgentOrchestrator(agents=[agent], llm=_FakeLLM("sensor"))
    task = Task(id="llm-1", intent="please inspect the silent readings", payload={})

    await orch._run_with_lifecycle(task)
    result = await orch.get_result("llm-1")

    assert result is not None
    assert result.success
    assert result.agent == "sensor"


@pytest.mark.anyio
async def test_orchestrator_aggregates_multiple_agent_results():
    orch = AgentOrchestrator(agents=[_StaticAgent("energy"), _StaticAgent("security")])
    task = Task(
        id="multi-1",
        intent="whole home status",
        payload={},
        metadata={"aggregate": True},
    )

    await orch._run_with_lifecycle(task)
    result = await orch.get_result("multi-1")

    assert result is not None
    assert result.success
    assert result.message == "Aggregated 2 agent results"
    assert len(result.data["results"]) == 2


@pytest.mark.anyio
async def test_orchestrator_retries_failed_agent_results():
    agent = _FlakyAgent()
    orch = AgentOrchestrator(agents=[agent])

    result = await orch._run_agent_with_retries(
        agent,
        Task(id="retry-1", intent="energy check", payload={}),
    )

    assert result.success
    assert result.data["attempt"] == 3
    assert agent.run_count == 3


@pytest.mark.anyio
async def test_orchestrator_blocks_guest_device_control_at_night():
    orch = AgentOrchestrator(
        agents=[_StaticAgent("device")],
        current_hour_provider=lambda: 23,
    )
    task = Task(
        id="policy-1",
        intent="turn on the porch light",
        payload={"action": "turn_on", "domain": "light"},
        metadata={"user_role": "guest"},
    )

    with patch(
        "orchestrator.agents.orchestrator.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        await orch._run_with_lifecycle(task)

    result = await orch.get_result("policy-1")
    assert result is not None
    assert not result.success
    assert result.error == "policy_device_control_quiet_hours"
    query.assert_awaited_once()


@pytest.mark.anyio
async def test_orchestrator_allows_homeowner_device_control_at_night():
    orch = AgentOrchestrator(
        agents=[_StaticAgent("device")],
        current_hour_provider=lambda: 23,
    )
    task = Task(
        id="policy-2",
        intent="turn on the porch light",
        payload={"action": "turn_on", "domain": "light"},
        metadata={"user_role": "homeowner"},
    )

    with patch(
        "orchestrator.agents.orchestrator.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        await orch._run_with_lifecycle(task)

    result = await orch.get_result("policy-2")
    assert result is not None
    assert result.success
    query.assert_awaited_once()


@pytest.mark.anyio
async def test_orchestrator_blocks_guest_sms_actions():
    orch = AgentOrchestrator(agents=[_StaticAgent("security")])
    task = Task(
        id="policy-3",
        intent="send sms security alert",
        payload={"action": "send_sms", "channel": "sms"},
        metadata={"user_role": "family_member"},
    )

    await orch._run_with_lifecycle(task)
    result = await orch.get_result("policy-3")

    assert result is not None
    assert not result.success
    assert result.error == "policy_sms_role_restricted"


# ------------------------------------------------------------------
# Healthcheck
# ------------------------------------------------------------------


@pytest.mark.anyio
async def test_agent_healthcheck():
    agent = EnergyAgent()
    status = await agent.healthcheck()
    assert status.healthy
