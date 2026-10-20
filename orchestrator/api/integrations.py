"""Unified household-context connector page."""

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_optional_current_user
from orchestrator.config import get_settings
from orchestrator.core.database import get_mysql_session
from orchestrator.core.google_calendar import current_calendar_context
from orchestrator.core.google_gmail import current_gmail_context
from orchestrator.core.home_snapshot import fetch_home_snapshot
from orchestrator.core.household_plan import create_household_plan
from orchestrator.core.household_plan import HouseholdPlan
from orchestrator.core.household_plan_feedback import (
    load_household_plan_preferences,
    save_household_plan_preference,
)
from orchestrator.core.weather import fetch_weather_snapshot

router = APIRouter(tags=["integrations"])
settings = get_settings()


class HouseholdPlanPreferenceRequest(BaseModel):
    """A resident-edited plan that guides later plans in the same category."""

    mode: str
    plan: HouseholdPlan


@router.get("/integrations", response_class=HTMLResponse)
async def integrations_page() -> HTMLResponse:
    """Serve the consolidated household-context connector workspace."""
    page = Path(__file__).resolve().parents[1] / "static" / "integrations.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/integrations/weather")
async def weather_summary() -> dict[str, Any]:
    """Return current Home Assistant weather and the next hourly forecast fact."""
    entity_id, current, forecasts = await fetch_weather_snapshot(settings)
    if not entity_id:
        return {"available": False, "warning": "Home Assistant weather is unavailable"}
    next_forecast = forecasts[0] if forecasts else None
    return {
        "available": True,
        "entity_id": entity_id,
        "current": current,
        "next_forecast": next_forecast,
    }


@router.get("/integrations/household-plan")
async def household_plan(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Build a recommended home plan from the connected household sources."""
    if current_user is not None:
        owner_id = current_user.id
    elif not settings.COMMAND_CENTER_AUTH_REQUIRED:
        owner_id = 0
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to view a household plan")

    # Calendar and Gmail share this request's SQLAlchemy session, so they must
    # not execute concurrently. The Home Assistant reads do not use that
    # session and can happen together afterward.
    calendar = await current_calendar_context(session, owner_id)
    gmail = await current_gmail_context(session, owner_id)
    preferences = await load_household_plan_preferences(session, user_id=owner_id)
    weather_data, snapshot = await asyncio.gather(
        fetch_weather_snapshot(settings),
        fetch_home_snapshot(settings),
    )
    weather_entity, current_weather, forecasts = weather_data
    weather = {
        "available": bool(weather_entity),
        "current": current_weather,
        "next_forecast": forecasts[0] if forecasts else None,
    }
    return await create_household_plan(
        calendar=calendar,
        gmail=gmail,
        weather=weather,
        snapshot=snapshot,
        preferences=preferences,
    )


@router.post("/integrations/household-plan/preference")
async def save_household_plan_preference_route(
    request: HouseholdPlanPreferenceRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Save a resident's plan edit for future plans of the same event category."""
    if request.mode not in {"away", "hosting", "current"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Plan category must be current, away, or hosting")
    if current_user is not None:
        owner_id = current_user.id
    elif not settings.COMMAND_CENTER_AUTH_REQUIRED:
        owner_id = 0
    else:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to save plan feedback")
    return await save_household_plan_preference(
        session,
        user_id=owner_id,
        mode=request.mode,
        plan=request.plan.model_dump(),
    )
