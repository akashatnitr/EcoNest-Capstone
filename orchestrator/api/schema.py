"""Local interactive view of the EcoNest MySQL schema."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["schema"])


@router.get("/schema", response_class=HTMLResponse)
async def schema_page() -> HTMLResponse:
    """Serve the read-only MySQL entity-relationship diagram."""
    page = Path(__file__).resolve().parents[1] / "static" / "schema.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))
