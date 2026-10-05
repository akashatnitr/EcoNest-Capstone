"""Tests for the internal MCP execution boundary used by agents."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from orchestrator.core.execution_trace import (
    finish_execution_trace,
    reset_execution_trace_context,
    set_execution_trace_context,
    start_model_execution_trace,
    task_execution_trace,
)
from orchestrator.core.permissions import Role
from orchestrator.mcp.executor import MCPToolExecutor, MCPToolPermissionError
from orchestrator.mcp.models import ToolExecutionResult


class _EchoInput(BaseModel):
    value: str


@pytest.mark.anyio
async def test_executor_validates_and_dispatches_registered_tool() -> None:
    handler = AsyncMock(
        return_value=ToolExecutionResult(capability="echo", result={"ok": True})
    )
    registry: dict[str, dict[str, Any]] = {
        "echo": {
            "input_schema": _EchoInput,
            "handler": handler,
            "permissions": ["device:read"],
        }
    }

    result = await MCPToolExecutor(registry).execute(
        "echo", {"value": "hello"}, role=Role.GUEST
    )

    assert result.success is True
    assert result.result == {"ok": True}
    handler.assert_awaited_once_with(_EchoInput(value="hello"))


@pytest.mark.anyio
async def test_executor_rejects_tool_without_required_permission() -> None:
    registry: dict[str, dict[str, Any]] = {
        "write": {
            "input_schema": _EchoInput,
            "handler": AsyncMock(),
            "permissions": ["device:write"],
        }
    }

    with pytest.raises(MCPToolPermissionError, match="device:write"):
        await MCPToolExecutor(registry).execute(
            "write", {"value": "blocked"}, role=Role.GUEST
        )


@pytest.mark.anyio
async def test_executor_records_safe_activity_trace() -> None:
    registry: dict[str, dict[str, Any]] = {
        "state": {
            "input_schema": _EchoInput,
            "handler": AsyncMock(
                return_value=ToolExecutionResult(capability="state", result={})
            ),
            "permissions": ["device:read"],
        }
    }

    with patch("orchestrator.mcp.executor.write_audit_event") as audit:
        await MCPToolExecutor(registry).execute(
            "state",
            {"value": "not-retained"},
            role=Role.GUEST,
            task_id="task-1",
            agent="device",
            source="http_api",
        )

    assert audit.call_args.args[0] == "mcp.tool.executed"
    event = audit.call_args.args[1]
    assert event["task_id"] == "task-1"
    assert event["agent"] == "device"
    assert event["arguments"] == {"keys": ["value"]}
    trace = task_execution_trace("task-1")
    assert trace[0]["name"] == "state"
    assert trace[0]["status"] == "completed"
    assert isinstance(trace[0]["duration_ms"], float)


def test_model_trace_uses_active_command_context() -> None:
    token = set_execution_trace_context("model-task", "event_history")
    try:
        task_id, trace_id = start_model_execution_trace("gemma3:4b")
        finish_execution_trace(task_id, trace_id, success=True)
    finally:
        reset_execution_trace_context(token)

    trace = task_execution_trace("model-task")
    assert len(trace) == 1
    assert trace[0]["kind"] == "model"
    assert trace[0]["name"] == "Gemma model response (gemma3:4b)"
    assert trace[0]["agent"] == "event_history"
    assert trace[0]["status"] == "completed"
    assert isinstance(trace[0]["duration_ms"], float)
