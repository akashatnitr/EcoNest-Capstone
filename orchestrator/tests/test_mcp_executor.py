"""Tests for the internal MCP execution boundary used by agents."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

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
