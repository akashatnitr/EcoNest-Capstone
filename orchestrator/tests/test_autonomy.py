"""Tests for background autonomous monitoring."""

import pytest
from unittest.mock import AsyncMock

from orchestrator.core import autonomy
from orchestrator.core.autonomy import AutonomousMonitor


async def test_autonomous_monitor_run_once_records_success(monkeypatch):
    events = []

    async def collect_feedback():
        return {
            "source": "ollama",
            "suggestions": [{"title": "Review load"}],
            "snapshot": {"occupancy_status": "home"},
        }

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
    )
    result = await monitor.run_once()

    assert result is not None
    assert monitor.status()["success_count"] == 1
    assert events == [
        (
            "autonomy.monitor.completed",
            {
                "success": True,
                "source": "ollama",
                "suggestion_count": 1,
                "occupancy_status": "home",
            },
        )
    ]


async def test_autonomous_monitor_run_once_records_failure(monkeypatch):
    events = []

    async def collect_feedback():
        raise RuntimeError("Home Assistant unavailable")

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
    )
    result = await monitor.run_once()

    assert result is None
    assert monitor.status()["failure_count"] == 1
    assert monitor.status()["last_error"] == "Home Assistant unavailable"
    assert events[0][0] == "autonomy.monitor.failed"
    assert events[0][1]["success"] is False
    assert events[0][1]["error"] == "RuntimeError"


async def test_autonomous_monitor_executes_high_confidence_action(monkeypatch):
    events = []
    executed = []

    async def collect_feedback():
        return {
            "source": "ollama",
            "suggestions": [{"title": "Media room light should be off"}],
            "snapshot": {"occupancy_status": "home"},
        }

    async def recommend_action(feedback):
        return {
            "confidence": 0.95,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
            "expected_outcome": {
                "entity_id": "light.upstairs_media_light_1",
                "state": "off",
            },
        }

    async def execute_action(recommendation):
        executed.append(recommendation)
        return {"success": True, "agent": "device"}

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        action_confidence_threshold=0.85,
        actions_enabled=True,
    )

    await monitor.run_once()

    assert len(executed) == 1
    assert monitor.status()["action_execution_count"] == 1
    assert [event[0] for event in events] == [
        "autonomy.monitor.completed",
        "autonomy.action.recommended",
        "autonomy.action.executed",
    ]

@pytest.mark.asyncio
async def test_autonomous_monitor_executes_through_device_agent_and_verifies(
    monkeypatch,
):
    from orchestrator.agents.device_agent import DeviceAgent, PermissionCheck, CapabilityCheck
    from orchestrator.agents.orchestrator import Task
    from orchestrator.mcp.models import ToolExecutionResult

    events = []
    tool_calls = []

    async def collect_feedback():
        return {
            "source": "ha_event_dispatcher",
            "suggestions": [
                {"title": "Media room light should be turned off"}
            ],
            "snapshot": {"occupancy_status": "home"},
        }

    async def recommend_action(feedback):
        return {
            "confidence": 0.95,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        }

    agent = DeviceAgent()
    agent._check_permission = AsyncMock(
        return_value=PermissionCheck(
            allowed=True,
            reason="Test permission granted",
        )
    )
    agent._check_capability = AsyncMock(
        return_value=CapabilityCheck(
            allowed=True,
            reason="Test capability granted",
        )
    )

    async def fake_invoke_mcp_tool(task, capability, input_data):
        tool_calls.append((capability, input_data))

        if capability == "ha_call_service":
            return ToolExecutionResult(
                capability="ha_call_service",
                result={"status": "ok"},
            )

        if capability == "ha_get_state":
            return ToolExecutionResult(
                capability="ha_get_state",
                result={
                    "entity_id": "light.upstairs_media_light_1",
                    "state": "off",
                    "attributes": {},
                },
            )

        raise AssertionError(f"Unexpected MCP capability: {capability}")

    agent.invoke_mcp_tool = fake_invoke_mcp_tool

    async def execute_action(recommendation):
        task = Task(
            id="level3-device-execution",
            intent="turn off media room light",
            payload=recommendation,
            metadata={
                "source": "autonomous_monitor",
            },
        )
        result = await agent.run(task)

        return {
            "success": result.success,
            "agent": "device",
            "verified": result.data["verified"],
            "execution_source": result.data["execution_source"],
        }

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(
        "orchestrator.core.autonomy.write_audit_event_async",
        fake_write,
    )

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        action_confidence_threshold=0.85,
        actions_enabled=True,
    )

    await monitor.run_once()

    assert monitor.status()["action_execution_count"] == 1

    assert [event[0] for event in events] == [
        "autonomy.monitor.completed",
        "autonomy.action.recommended",
        "autonomy.action.executed",
    ]

    assert tool_calls[0] == (
        "ha_call_service",
        {
            "domain": "light",
            "service": "turn_off",
            "entity_id": "light.upstairs_media_light_1",
            "service_data": None,
        },
    )

    assert tool_calls[1] == (
        "ha_get_state",
        {
            "entity_id": "light.upstairs_media_light_1",
        },
    )

    assert events[-1][1]["success"] is True
    assert events[-1][1]["result"]["verified"] is True

async def test_autonomous_monitor_skips_low_confidence_action(monkeypatch):
    events = []

    async def collect_feedback():
        return {"source": "ollama", "suggestions": [], "snapshot": {}}

    async def recommend_action(feedback):
        return {
            "confidence": 0.5,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        }

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_confidence_threshold=0.85,
    )

    await monitor.run_once()

    assert monitor.status()["action_skip_count"] == 1
    assert events[-1][0] == "autonomy.action.skipped"
    assert events[-1][1]["reason"] == "confidence_below_threshold"


@pytest.mark.asyncio
async def test_autonomous_monitor_records_but_does_not_execute_when_disabled(monkeypatch):
    """Observe-only mode must retain recommendations without device control."""
    events = []
    executed = []

    async def collect_feedback():
        return {"source": "ollama", "suggestions": [], "snapshot": {}}

    async def recommend_action(feedback):
        return {"confidence": 0.95, "entity_id": "light.safe", "action": "turn_off"}

    async def execute_action(recommendation):
        executed.append(recommendation)
        return {"success": True}

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)
    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        actions_enabled=False,
    )

    await monitor.run_once()

    assert executed == []
    assert monitor.status()["action_skip_count"] == 1
    assert events[-1][1]["reason"] == "actions_disabled"


@pytest.mark.asyncio
async def test_autonomous_monitor_blocks_low_confidence_action(monkeypatch):
    events = []
    executions = []

    async def collect_feedback():
        return {"source": "ha_event_dispatcher"}

    async def recommend_action(feedback):
        return {
            "confidence": 0.50,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        }

    async def execute_action(recommendation):
        executions.append(recommendation)
        return {"success": True}

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(
        "orchestrator.core.autonomy.write_audit_event_async",
        fake_write,
    )

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        action_confidence_threshold=0.85,
        actions_enabled=True,
    )

    await monitor.run_once()

    assert executions == []
    assert monitor.status()["action_execution_count"] == 0
    assert any(
        event[0] == "autonomy.action.recommended"
        for event in events
    )

@pytest.mark.asyncio
async def test_autonomous_monitor_blocks_when_actions_disabled(monkeypatch):
    executions = []

    async def collect_feedback():
        return {"source": "ha_event_dispatcher"}

    async def recommend_action(feedback):
        return {
            "confidence": 0.95,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        }

    async def execute_action(recommendation):
        executions.append(recommendation)
        return {"success": True}

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        action_confidence_threshold=0.85,
        actions_enabled=False,
    )

    await monitor.run_once()

    assert executions == []
    assert monitor.status()["action_execution_count"] == 0

@pytest.mark.asyncio
async def test_device_agent_blocks_when_home_assistant_action_fails():
    from orchestrator.agents.device_agent import (
        DeviceAgent,
        PermissionCheck,
        CapabilityCheck,
    )
    from orchestrator.agents.orchestrator import Task
    from orchestrator.mcp.models import ToolExecutionResult

    agent = DeviceAgent()

    agent._check_capability = AsyncMock(
        return_value=CapabilityCheck(
            allowed=True,
            reason="Test capability granted",
        )
    )

    agent._check_permission = AsyncMock(
        return_value=PermissionCheck(
            allowed=True,
            reason="Test permission granted",
        )
    )

    async def fake_invoke_mcp_tool(task, capability, input_data):
        assert capability == "ha_call_service"
        return ToolExecutionResult(
            capability="ha_call_service",
            result={"status": "error", "message": "HA unavailable"},
            success=False,
        )

    agent.invoke_mcp_tool = fake_invoke_mcp_tool

    task = Task(
        id="level3-ha-failure",
        intent="turn off media room light",
        payload={
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        },
        metadata={"source": "autonomous_monitor"},
    )

    result = await agent.run(task)

    assert result.success is False
    assert result.data["verified"] is False

@pytest.mark.asyncio
async def test_device_agent_blocks_when_home_assistant_verification_fails():
    from orchestrator.agents.device_agent import (
        DeviceAgent,
        PermissionCheck,
        CapabilityCheck,
    )
    from orchestrator.agents.orchestrator import Task
    from orchestrator.mcp.models import ToolExecutionResult

    agent = DeviceAgent()

    agent._check_capability = AsyncMock(
        return_value=CapabilityCheck(
            allowed=True,
            reason="Test capability granted",
        )
    )

    agent._check_permission = AsyncMock(
        return_value=PermissionCheck(
            allowed=True,
            reason="Test permission granted",
        )
    )

    async def fake_invoke_mcp_tool(task, capability, input_data):
        if capability == "ha_call_service":
            return ToolExecutionResult(
                capability="ha_call_service",
                result={"status": "ok"},
            )

        if capability == "ha_get_state":
            return ToolExecutionResult(
                capability="ha_get_state",
                result={
                    "entity_id": "light.upstairs_media_light_1",
                    "state": "on",
                    "attributes": {},
                },
            )

        raise AssertionError(f"Unexpected capability: {capability}")

    agent.invoke_mcp_tool = fake_invoke_mcp_tool

    task = Task(
        id="level3-verification-failure",
        intent="turn off media room light",
        payload={
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        },
        metadata={"source": "autonomous_monitor"},
    )

    result = await agent.run(task)

    assert result.success is False
    assert result.data["verified"] is False