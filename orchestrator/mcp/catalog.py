"""Built-in MCP capabilities shared by HTTP clients and internal agents."""

from orchestrator.mcp.prompts import (
    DEVICE_CONTROL_PROMPT,
    ENERGY_REVIEW_PROMPT,
    SECURITY_CHECK_PROMPT,
    SENSOR_HEALTH_PROMPT,
)
from orchestrator.mcp.registry import register_prompt, register_resource, register_tool
from orchestrator.mcp.resources import (
    home_analytics_resource,
    home_devices_resource,
    home_snapshot_resource,
    ontology_resource,
    recent_memory_resource,
)
from orchestrator.mcp.tools import db_tools, device_tools, graph_tools, ha_tools


def register_builtin_mcp_components() -> None:
    """Register capabilities used by both external clients and EcoNest agents."""
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
        "Run a read-only Gremlin or SQL query against ArcadeDB",
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
        "record_device_action",
        "Record a device-control outcome in the graph audit trail",
        graph_tools.RecordDeviceActionInput,
        graph_tools.record_device_action_handler,
        permissions=["agent:run"],
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

    register_resource(
        "home://snapshot", "Current smart-home snapshot", home_snapshot_resource
    )
    register_resource(
        "home://devices", "Device inventory and state", home_devices_resource
    )
    register_resource(
        "home://analytics", "Home analytics context", home_analytics_resource
    )
    register_resource(
        "home://ontology", "Smart-home ontology context", ontology_resource
    )
    register_resource(
        "home://memory/recent",
        "Recent episodic memory summaries and interactions",
        recent_memory_resource,
    )

    register_prompt("energy_review", ENERGY_REVIEW_PROMPT)
    register_prompt("security_check", SECURITY_CHECK_PROMPT)
    register_prompt("device_control", DEVICE_CONTROL_PROMPT)
    register_prompt("sensor_health", SENSOR_HEALTH_PROMPT)
