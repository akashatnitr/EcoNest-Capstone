"""Shared MCP dispatch boundary for external clients and internal agents."""

from time import monotonic
from typing import Any

from pydantic import ValidationError

from orchestrator.core.permissions import Role, has_permission
from orchestrator.core.audit import write_audit_event
from orchestrator.mcp.catalog import register_builtin_mcp_components
from orchestrator.mcp.models import ToolExecutionResult
from orchestrator.mcp.registry import resource_registry, tool_registry


class MCPToolError(Exception):
    """Base error raised when MCP tool dispatch cannot proceed."""


class MCPToolNotFoundError(MCPToolError):
    """Raised when the requested MCP tool is not registered."""


class MCPToolPermissionError(MCPToolError):
    """Raised when the caller lacks a tool's required permission."""


class MCPToolValidationError(MCPToolError):
    """Raised when arguments do not match a tool's input schema."""


class MCPResourceNotFoundError(MCPToolError):
    """Raised when the requested MCP resource is not registered."""


class MCPToolExecutor:
    """Invoke registered MCP capabilities without an internal HTTP loopback."""

    def __init__(self, registry: dict[str, dict[str, Any]] | None = None) -> None:
        if registry is None:
            register_builtin_mcp_components()
        self._tool_registry = registry if registry is not None else tool_registry

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        role: Role | str,
        task_id: str = "",
        agent: str = "",
        source: str = "internal",
    ) -> ToolExecutionResult:
        """Validate permissions and arguments before invoking one MCP tool."""
        started_at = monotonic()
        try:
            meta = self._tool_registry.get(name)
            if meta is None:
                raise MCPToolNotFoundError(f"Tool '{name}' not found")
            permissions = meta["permissions"]
            missing = next(
                (
                    permission
                    for permission in permissions
                    if not has_permission(role, permission)
                ),
                None,
            )
            if missing is not None:
                raise MCPToolPermissionError(f"Missing permission: {missing}")
            try:
                parsed = meta["input_schema"](**arguments)
            except ValidationError as exc:
                raise MCPToolValidationError(str(exc)) from exc
            result = await meta["handler"](parsed)
            if isinstance(result, ToolExecutionResult):
                execution = result
            elif isinstance(result, dict) and "capability" in result:
                execution = ToolExecutionResult.model_validate(result)
            else:
                execution = ToolExecutionResult(capability=name, result=result)
        except Exception as exc:
            self._record_tool_execution(
                name,
                arguments,
                task_id=task_id,
                agent=agent,
                source=source,
                success=False,
                duration_ms=_duration_ms(started_at),
                error=str(exc),
            )
            raise

        self._record_tool_execution(
            name,
            arguments,
            task_id=task_id,
            agent=agent,
            source=source,
            success=execution.success,
            duration_ms=_duration_ms(started_at),
            warnings=execution.warnings,
        )
        return execution

    async def read_resource(
        self,
        uri: str,
        *,
        user_id: str = "",
        task_id: str = "",
        agent: str = "",
        source: str = "internal",
    ) -> dict[str, Any]:
        """Read a registered MCP resource through the shared catalog."""
        started_at = monotonic()
        try:
            register_builtin_mcp_components()
            resource = resource_registry.get(uri)
            if resource is None:
                raise MCPResourceNotFoundError(f"Resource '{uri}' not found")
            if uri == "home://memory/recent":
                result = await resource["handler"](user_id)
            else:
                result = await resource["handler"]()
        except Exception as exc:
            self._record_resource_read(
                uri,
                task_id=task_id,
                agent=agent,
                source=source,
                success=False,
                duration_ms=_duration_ms(started_at),
                error=str(exc),
            )
            raise

        self._record_resource_read(
            uri,
            task_id=task_id,
            agent=agent,
            source=source,
            success=True,
            duration_ms=_duration_ms(started_at),
        )
        return result

    def _record_tool_execution(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        task_id: str,
        agent: str,
        source: str,
        success: bool,
        duration_ms: float,
        warnings: list[str] | None = None,
        error: str = "",
    ) -> None:
        write_audit_event(
            "mcp.tool.executed",
            {
                "task_id": task_id,
                "agent": agent,
                "source": source,
                "tool": name,
                "success": success,
                "duration_ms": duration_ms,
                "arguments": _safe_argument_summary(arguments),
                "warnings": warnings or [],
                "error": error[:300] if error else None,
            },
        )

    def _record_resource_read(
        self,
        uri: str,
        *,
        task_id: str,
        agent: str,
        source: str,
        success: bool,
        duration_ms: float,
        error: str = "",
    ) -> None:
        write_audit_event(
            "mcp.resource.read",
            {
                "task_id": task_id,
                "agent": agent,
                "source": source,
                "resource": uri,
                "success": success,
                "duration_ms": duration_ms,
                "error": error[:300] if error else None,
            },
        )


def _duration_ms(started_at: float) -> float:
    return round((monotonic() - started_at) * 1000, 3)


def _safe_argument_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep traces useful without retaining raw queries, tokens, or payloads."""
    summary: dict[str, Any] = {"keys": sorted(arguments)}
    for key in ("entity_id", "device_id", "domain", "service", "language"):
        value = arguments.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value
    if "query" in arguments:
        summary["query_recorded"] = False
    if isinstance(arguments.get("params"), dict):
        summary["parameter_keys"] = sorted(arguments["params"])
    return summary
