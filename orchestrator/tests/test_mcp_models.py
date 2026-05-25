from orchestrator.mcp.models import ToolExecutionResult


def test_tool_execution_result_defaults():
    result = ToolExecutionResult(
        capability="test_capability",
    )

    assert result.success is True
    assert result.confidence == 1.0
