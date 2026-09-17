"""Tests for MCP graph tool safety and runtime audit storage."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.mcp.tools.graph_tools import (
    RecordDeviceActionInput,
    record_device_action_handler,
)


@pytest.mark.anyio
async def test_record_device_action_uses_runtime_vertex_type() -> None:
    with patch(
        "orchestrator.mcp.tools.graph_tools.arcadedb_query",
        new=AsyncMock(return_value={"result": []}),
    ) as query:
        result = await record_device_action_handler(
            RecordDeviceActionInput(
                action="turn_off",
                task_id="task-1",
                user_id="service",
                success=True,
            )
        )

    commands = [call.args[1] for call in query.await_args_list]
    assert result.success is True
    assert "CREATE VERTEX TYPE ActionExecution IF NOT EXISTS" in commands
    action_command = next(
        command
        for command in commands
        if command.startswith("CREATE VERTEX ActionExecution SET")
    )
    assert "timestamp = '" in action_command
    assert "datetime()" not in action_command
    assert not any(command.startswith("CREATE VERTEX Action SET") for command in commands)
