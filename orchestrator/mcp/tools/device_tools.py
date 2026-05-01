"""MCP tools for device control operations."""

from pydantic import BaseModel, Field

from orchestrator.core.database import arcadedb_query


class DeviceActionInput(BaseModel):
    device_id: str


class DeviceBrightnessInput(BaseModel):
    device_id: str
    brightness: int = Field(ge=0, le=100)




async def device_turn_on_handler(input_data: DeviceActionInput) -> dict:
    """Turn on a device."""
    await arcadedb_query(
        "sql",
        f"UPDATE Device SET is_active = true WHERE @rid = '{input_data.device_id}'",
        readonly=False,
    )
    return {"device_id": input_data.device_id, "state": "on"}


async def device_turn_off_handler(input_data: DeviceActionInput) -> dict:
    """Turn off a device."""
    await arcadedb_query(
        "sql",
        f"UPDATE Device SET is_active = false WHERE @rid = '{input_data.device_id}'",
        readonly=False,
    )
    return {"device_id": input_data.device_id, "state": "off"}


async def device_set_brightness_handler(input_data: DeviceBrightnessInput) -> dict:
    """Set brightness of a dimmable device."""
    await arcadedb_query(
        "sql",
        (
            f"UPDATE Device SET brightness = {input_data.brightness} "
            f"WHERE @rid = '{input_data.device_id}'"
        ),
        readonly=False,
    )
    return {
        "device_id": input_data.device_id,
        "brightness": input_data.brightness,
    }


async def device_get_status_handler(input_data: DeviceActionInput) -> dict:
    """Get status of a device."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{input_data.device_id}').valueMap()",
    )
    rows = result.get("result", [])
    return rows[0] if rows else {"error": "Device not found"}
