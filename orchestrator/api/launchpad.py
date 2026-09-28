"""EcoNest service launchpad routes."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["launchpad"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/launchpad", response_class=HTMLResponse)
async def launchpad_page() -> HTMLResponse:
    """Serve one easy-access page for all EcoNest services."""
    page = Path(__file__).resolve().parents[1] / "static" / "launchpad.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))
