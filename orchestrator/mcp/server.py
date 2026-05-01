"""MCP protocol implementation (tools/resources/prompts)."""

from typing import Annotated, Any, Callable, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import has_permission
from orchestrator.mcp.tools import db_tools, device_tools, graph_tools, ha_tools

router = APIRouter(prefix="/mcp", tags=["mcp"])

# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

ToolHandler = Callable[..., Any]
_tool_registry: Dict[str, Dict[str, Any]] = {}


def register_tool(
    name: str,
    description: str,
    input_schema: type[BaseModel],
    handler: ToolHandler,
    permissions: List[str] | None = None,
) -> None:
    """Register an MCP tool with metadata and a handler function."""
    _tool_registry[name] = {
        "description": description,
        "input_schema": input_schema,
        "handler": handler,
        "permissions": permissions or [],
    }


# ------------------------------------------------------------------
# Built-in tools registration
# ------------------------------------------------------------------

register_tool(
    "query_mysql",
    "Run a read-only SQL query against MySQL",
    db_tools.QueryMySQLInput,
    db_tools.query_mysql_handler,
    permissions=["device:read"],
)

register_tool(
    "get_readings",
    "Get sensor readings for a device",
    db_tools.GetReadingsInput,
    db_tools.get_readings_handler,
    permissions=["device:read"],
)

register_tool(
    "query_arcadedb",
    "Run a read-only Gremlin query against ArcadeDB",
    graph_tools.QueryArcadeDBInput,
    graph_tools.query_arcadedb_handler,
    permissions=["device:read"],
)

register_tool(
    "get_device_neighbors",
    "Get related devices/circuits/rooms for a device",
    graph_tools.GetDeviceNeighborsInput,
    graph_tools.get_device_neighbors_handler,
    permissions=["device:read"],
)

register_tool(
    "ha_get_state",
    "Get current state of a Home Assistant entity",
    ha_tools.HAGetStateInput,
    ha_tools.ha_get_state_handler,
    permissions=["device:read"],
)

register_tool(
    "ha_call_service",
    "Call a Home Assistant service",
    ha_tools.HACallServiceInput,
    ha_tools.ha_call_service_handler,
    permissions=["device:write"],
)

register_tool(
    "device_turn_on",
    "Turn on a device by ID",
    device_tools.DeviceActionInput,
    device_tools.device_turn_on_handler,
    permissions=["device:write"],
)

register_tool(
    "device_turn_off",
    "Turn off a device by ID",
    device_tools.DeviceActionInput,
    device_tools.device_turn_off_handler,
    permissions=["device:write"],
)

register_tool(
    "device_set_brightness",
    "Set brightness of a dimmable device",
    device_tools.DeviceBrightnessInput,
    device_tools.device_set_brightness_handler,
    permissions=["device:write"],
)

register_tool(
    "device_get_status",
    "Get status of a device",
    device_tools.DeviceActionInput,
    device_tools.device_get_status_handler,
    permissions=["device:read"],
)


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


class ToolInvokeRequest(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class ResourceRequest(BaseModel):
    uri: str


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.get("/tools")
async def list_tools(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """List available MCP tools filtered by user permissions."""
    tools = []
    for name, meta in _tool_registry.items():
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
):
    """Invoke an MCP tool directly (with auth)."""
    if name not in _tool_registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{name}' not found",
        )
    meta = _tool_registry[name]
    for perm in meta["permissions"]:
        if not has_permission(current_user.role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {perm}",
            )
    try:
        parsed = meta["input_schema"](**req.arguments)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid arguments: {exc}",
        )
    result = await meta["handler"](parsed)
    return {"tool": name, "result": result}


@router.get("/resources/{uri:path}")
async def get_resource(
    uri: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Get resource snapshot by URI."""
    if uri == "home://snapshot":
        return {"type": "snapshot", "rooms": [], "active_devices": []}
    if uri == "home://devices":
        return {"type": "devices", "count": 0}
    if uri == "home://analytics":
        return {"type": "analytics", "hourly_power": []}
    if uri == "home://ontology":
        return {"type": "ontology", "classes": []}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Resource '{uri}' not found",
    )


@router.get("/prompts/{name}")
async def get_prompt(
    name: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Return a prompt template by name."""
    prompts = {
        "energy_review": "Review the last 24h of energy usage and highlight any off-schedule devices.",
        "security_check": "Check all motion sensors and garage doors for anomalies in the last hour.",
        "device_control": "The user wants to control a device. Ask for clarification if the request is ambiguous.",
        "sensor_health": "Review sensor data quality and flag any sensors that haven't reported in >15 minutes.",
    }
    if name not in prompts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prompt '{name}' not found",
        )
    return {"name": name, "text": prompts[name]}
