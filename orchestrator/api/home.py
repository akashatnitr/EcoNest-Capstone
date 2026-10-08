"""Home snapshot API."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.responses import HTMLResponse

from orchestrator.config import get_settings
from orchestrator.core.home_snapshot import build_home_snapshot

import httpx

settings = get_settings()

router = APIRouter(prefix="/home", tags=["home"])


async def _read_home_assistant_states() -> list[dict[str, Any]]:
    if not settings.HA_TOKEN:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{settings.HA_URL.rstrip('/')}/api/states",
            headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
        )
        response.raise_for_status()

    data = response.json()
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


@router.get("/snapshot/view", response_class=HTMLResponse)
async def home_snapshot_view() -> HTMLResponse:
    """Render the human-facing home snapshot."""
    page = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "home-snapshot.html"
    )
    return HTMLResponse(page.read_text())


@router.get("/snapshot")
async def home_snapshot() -> dict[str, Any]:
    """Return the current smart-home snapshot."""
    if not settings.HA_TOKEN:
        return {
            "type": "snapshot",
            "available": False,
            "warnings": ["Home Assistant is not configured"],
        }

    try:
        states = await _read_home_assistant_states()
    except httpx.HTTPError:
        return {
            "type": "snapshot",
            "available": False,
            "warnings": ["Home Assistant snapshot is unavailable"],
        }

    return {
        "type": "snapshot",
        "available": True,
        **build_home_snapshot(states),
    }
