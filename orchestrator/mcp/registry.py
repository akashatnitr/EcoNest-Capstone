"""Central MCP registries for tools, resources, and prompts."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

ToolHandler = Callable[..., Awaitable[Any]]
ResourceHandler = Callable[..., Awaitable[Any]]

tool_registry: dict[str, dict[str, Any]] = {}
resource_registry: dict[str, dict[str, Any]] = {}
prompt_registry: dict[str, str] = {}


def register_tool(
    name: str,
    description: str,
    input_schema: type[BaseModel],
    handler: ToolHandler,
    permissions: list[str] | None = None,
) -> None:
    tool_registry[name] = {
        "description": description,
        "input_schema": input_schema,
        "handler": handler,
        "permissions": permissions or [],
    }


def register_resource(
    uri: str,
    description: str,
    handler: ResourceHandler,
) -> None:
    resource_registry[uri] = {
        "description": description,
        "handler": handler,
    }


def register_prompt(
    name: str,
    text: str,
) -> None:
    prompt_registry[name] = text
