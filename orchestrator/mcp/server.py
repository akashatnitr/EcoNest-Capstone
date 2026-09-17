"""MCP protocol implementation (tools/resources/prompts)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import has_permission
from orchestrator.mcp.catalog import register_builtin_mcp_components
from orchestrator.mcp.executor import (
    MCPToolExecutor,
    MCPToolNotFoundError,
    MCPToolPermissionError,
    MCPToolValidationError,
)
from orchestrator.mcp.registry import (
    prompt_registry,
    resource_registry,
    tool_registry,
)

router = APIRouter(prefix="/mcp", tags=["mcp"])

register_builtin_mcp_components()

# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class ToolInvokeRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ResourceRequest(BaseModel):
    uri: str


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.get("/tools")
async def list_tools(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """List available MCP tools filtered by user permissions."""
    tools = []
    for name, meta in tool_registry.items():
        if all(has_permission(current_user.role, p) for p in meta["permissions"]):
            tools.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "input_schema": meta["input_schema"].model_json_schema(),
                }
            )
    return {"tools": tools}


@router.post("/tools/{name}")
async def invoke_tool(
    name: str,
    req: ToolInvokeRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Invoke an MCP tool directly (with auth)."""
    try:
        result = await MCPToolExecutor(tool_registry).execute(
            name,
            req.arguments,
            role=current_user.role,
            source="mcp_api",
        )
    except MCPToolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except MCPToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except MCPToolValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid arguments: {exc}",
        )
    return {
        "tool": name,
        "execution": (result.model_dump() if hasattr(result, "model_dump") else result),
    }


@router.get("/resources/{uri:path}")
async def get_resource(
    uri: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get resource snapshot by URI."""
    if uri not in resource_registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource '{uri}' not found",
        )

    resource = resource_registry[uri]

    if uri == "home://memory/recent":
        result = await resource["handler"](str(current_user.id))
    else:
        result = await resource["handler"]()

    return {
        "uri": uri,
        "resource": result,
    }


@router.get("/prompts/{name}")
async def get_prompt(
    name: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    if name not in prompt_registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt '{name}' not found",
        )

    return {
        "name": name,
        "text": prompt_registry[name],
    }
