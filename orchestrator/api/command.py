"""Browser command-console route."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["command"])


@router.get("/command", response_class=HTMLResponse)
async def command_page() -> HTMLResponse:
    """Serve the authenticated natural-language command console."""
    page = Path(__file__).resolve().parents[1] / "static" / "command.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))
