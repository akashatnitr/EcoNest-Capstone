"""Home snapshot API."""

from pathlib import Path
from typing import Any
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.config import get_settings
from orchestrator.core.home_snapshot import fetch_home_snapshot

settings = get_settings()

router = APIRouter(prefix="/home", tags=["home"])


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
    return await fetch_home_snapshot(settings)
