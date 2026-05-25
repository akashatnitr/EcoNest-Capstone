from orchestrator.mcp.registry import (
    prompt_registry,
    resource_registry,
    tool_registry,
)


def test_tool_registry_exists():
    assert isinstance(tool_registry, dict)


def test_resource_registry_exists():
    assert isinstance(resource_registry, dict)


def test_prompt_registry_exists():
    assert isinstance(prompt_registry, dict)
