"""MCP tools for device control operations."""

from typing import Any

from pydantic import BaseModel, Field

from orchestrator.core.database import arcadedb_query


class DeviceActionInput(BaseModel):
    device_id: str


class DeviceBrightnessInput(BaseModel):
    device_id: str
    brightness: int = Field(ge=0, le=100)


def _escape_arcadedb_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _device_rid(input_data: DeviceActionInput | DeviceBrightnessInput) -> str:
    return _escape_arcadedb_string(input_data.device_id)


async def device_turn_on_handler(input_data: DeviceActionInput) -> dict[str, Any]:
    """Turn on a device."""
    await arcadedb_query(
        "sql",
        f"UPDATE Device SET is_active = true WHERE @rid = '{_device_rid(input_data)}'",
        readonly=False,
    )
    return {"device_id": input_data.device_id, "state": "on"}


async def device_turn_off_handler(input_data: DeviceActionInput) -> dict[str, Any]:
    """Turn off a device."""
    await arcadedb_query(
        "sql",
        f"UPDATE Device SET is_active = false WHERE @rid = '{_device_rid(input_data)}'",
        readonly=False,
    )
    return {"device_id": input_data.device_id, "state": "off"}


async def device_set_brightness_handler(
    input_data: DeviceBrightnessInput,
) -> dict[str, Any]:
    """Set brightness of a dimmable device."""
    await arcadedb_query(
        "sql",
        (
            f"UPDATE Device SET brightness = {input_data.brightness} "
            f"WHERE @rid = '{_device_rid(input_data)}'"
        ),
        readonly=False,
    )
    return {
        "device_id": input_data.device_id,
        "brightness": input_data.brightness,
    }


async def device_get_status_handler(input_data: DeviceActionInput) -> dict[str, Any]:
    """Get status of a device."""
    result = await arcadedb_query(
        "gremlin",
        f"g.V('{_device_rid(input_data)}').valueMap(true)",
    )
    rows = result.get("result", [])
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {"error": "Device not found"}
