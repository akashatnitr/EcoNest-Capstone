"""EcoNest service launchpad routes."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["launchpad"])


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page() -> HTMLResponse:
    """Render the browser sign-in page for the protected EcoNest workspace."""
    page = Path(__file__).resolve().parents[1] / "static" / "login.html"
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/launchpad", response_class=HTMLResponse)
async def launchpad_page() -> HTMLResponse:
    """Serve one easy-access page for all EcoNest services."""
    page = Path(__file__).resolve().parents[1] / "static" / "launchpad.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))
