"""MCP tools for Home Assistant operations."""

import os
from typing import Any

import httpx
from pydantic import BaseModel

from orchestrator.mcp.models import ToolExecutionResult
from orchestrator.config import get_settings

settings = get_settings()


class HAGetStateInput(BaseModel):
    entity_id: str


class HACallServiceInput(BaseModel):
    domain: str
    service: str
    entity_id: str
    service_data: dict | None = None


def _json_object(response: httpx.Response) -> dict[str, Any]:
    data = response.json()
    if isinstance(data, dict):
        return data
    return {"result": data}


async def ha_get_state_handler(input_data: HAGetStateInput) -> dict[str, Any]:
    """Get current state of a Home Assistant entity."""
    token = settings.HA_TOKEN or os.getenv("HA_TOKEN", "")
    if not token:
        return ToolExecutionResult(
            success=False,
            capability="ha_get_state",
            warnings=["HA_TOKEN not configured"],
        )
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.HA_URL}/api/states/{input_data.entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            return ToolExecutionResult(
                capability="ha_get_state",
                result=_json_object(response),
                metadata={
                    "entity_id": input_data.entity_id,
                    "source": "home_assistant",
                },
            )
        return ToolExecutionResult(
            success=False,
            capability="ha_get_state",
            warnings=[f"HA API returned {response.status_code}"],
        )


async def ha_call_service_handler(input_data: HACallServiceInput) -> dict[str, Any]:
    """Call a Home Assistant service."""
    token = settings.HA_TOKEN or os.getenv("HA_TOKEN", "")
    if not token:
        return ToolExecutionResult(
            success=False,
            capability="ha_call_service",
            warnings=["HA_TOKEN not configured"],
        )
    payload = {"entity_id": input_data.entity_id}
    if input_data.service_data:
        payload.update(input_data.service_data)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{settings.HA_URL}/api/services/{input_data.domain}/{input_data.service}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code in (200, 201):
            return ToolExecutionResult(
                capability="ha_call_service",
                result={
                    "status": "ok",
                    "response": _json_object(response),
                },
                confidence=0.9,
                metadata={
                    "domain": input_data.domain,
                    "service": input_data.service,
                    "entity_id": input_data.entity_id,
                },
            )
        return ToolExecutionResult(
            success=False,
            capability="ha_call_service",
            warnings=[f"HA API returned {response.status_code}"],
        )
