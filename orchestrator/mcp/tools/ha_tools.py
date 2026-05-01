"""MCP tools for Home Assistant operations."""

import os

import httpx
from pydantic import BaseModel

from orchestrator.config import get_settings

settings = get_settings()


class HAGetStateInput(BaseModel):
    entity_id: str


class HACallServiceInput(BaseModel):
    domain: str
    service: str
    entity_id: str
    service_data: dict | None = None


async def ha_get_state_handler(input_data: HAGetStateInput) -> dict:
    """Get current state of a Home Assistant entity."""
    token = settings.HA_TOKEN or os.getenv("HA_TOKEN", "")
    if not token:
        return {"error": "HA_TOKEN not configured"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.HA_URL}/api/states/{input_data.entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            return response.json()
        return {"error": f"HA API returned {response.status_code}"}


async def ha_call_service_handler(input_data: HACallServiceInput) -> dict:
    """Call a Home Assistant service."""
    token = settings.HA_TOKEN or os.getenv("HA_TOKEN", "")
    if not token:
        return {"error": "HA_TOKEN not configured"}
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
            return {"status": "ok", "response": response.json()}
        return {"error": f"HA API returned {response.status_code}"}
