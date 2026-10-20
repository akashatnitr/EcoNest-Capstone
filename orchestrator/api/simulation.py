"""Safe visual simulation workspace for seeded EcoNest households."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import ACTIVE_HOUSEHOLD_COOKIE_NAME, UserProfile, get_current_user
from orchestrator.core.database import get_mysql_session
from orchestrator.core.permissions import USER_ADMIN, has_permission
from orchestrator.core.simulated_households import (
    CASAS_ARUBA_NAME,
    CITY_APARTMENT_NAME,
    CN_OBEE_NAME,
)
from orchestrator.core.simulation_runtime import (
    SimulationAction,
    apply_dataset_manual_override,
    apply_simulation_action,
    simulated_runtime_state,
)

router = APIRouter(prefix="/simulation", tags=["simulation"])


class SimulationEventRequest(BaseModel):
    """One bounded event a user may trigger in the City Apartment demo."""

    action: SimulationAction
    household_id: int | None = None


class SimulationPromptRequest(BaseModel):
    """Natural-language prompt evaluated against a simulated household only."""

    prompt: str
    household_id: int | None = None


class SimulationOverrideRequest(BaseModel):
    """A safe, manual dataset-simulation device or resident-position override."""

    household_id: int | None = None
    device_id: str | None = None
    device_state: str | None = None
    resident_room: str | None = None


@router.get("", response_class=HTMLResponse)
async def simulation_page() -> HTMLResponse:
    """Render the household digital-twin workspace."""
    page = Path(__file__).resolve().parents[1] / "static" / "simulation.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/workspace", response_class=HTMLResponse)
async def simulation_workspace_page() -> HTMLResponse:
    """Render a dataset-backed replacement for a live-home workspace page."""
    page = Path(__file__).resolve().parents[1] / "static" / "simulation-workspace.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/command", response_class=HTMLResponse)
async def simulation_command_page() -> HTMLResponse:
    """Render the simulated-household prompt experience."""
    page = Path(__file__).resolve().parents[1] / "static" / "simulation-command.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/autonomy", response_class=HTMLResponse)
async def simulation_autonomy_page() -> HTMLResponse:
    """Render the simulated-household autonomous-actions experience."""
    page = Path(__file__).resolve().parents[1] / "static" / "simulation-autonomy.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/insights", response_class=HTMLResponse)
async def simulation_insights_page() -> HTMLResponse:
    """Render the simulated-household insights experience."""
    page = Path(__file__).resolve().parents[1] / "static" / "simulation-insights.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/state")
async def simulation_state(
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
    household_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Return the selected simulated household's safe initial state."""
    resolved = await _simulated_household(
        request, current_user, session, requested_household_id=household_id
    )
    if resolved is None:
        return _unavailable_state()
    household_id, household_name = resolved
    return {"available": True, "household_id": household_id, **await simulated_runtime_state(household_id, household_name)}


@router.post("/events")
async def trigger_simulation_event(
    payload: SimulationEventRequest,
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Apply one safe, bounded event without touching live-home services."""
    resolved = await _simulated_household(
        request,
        current_user,
        session,
        requested_household_id=payload.household_id,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A supported simulated household is not the active household.",
        )
    household_id, household_name = resolved
    return {
        "available": True,
        "household_id": household_id,
        **await apply_simulation_action(household_id, household_name, payload.action),
    }


@router.post("/override")
async def override_simulation_state(
    payload: SimulationOverrideRequest,
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Update only manual dataset simulation state; never real devices."""
    resolved = await _simulated_household(
        request, current_user, session, requested_household_id=payload.household_id
    )
    if resolved is None or resolved[1] not in {CASAS_ARUBA_NAME, CN_OBEE_NAME}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select CASAS Aruba Replay or CN-OBEE Beijing Apartment.",
        )
    if payload.device_id is None and payload.resident_room is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a device or resident location.")
    try:
        state = await apply_dataset_manual_override(
            resolved[0], resolved[1], device_id=payload.device_id, device_state=payload.device_state,
            resident_room=payload.resident_room,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"available": True, "household_id": resolved[0], **state}


@router.get("/recommendations")
async def simulation_recommendations(
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
    household_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Derive safe energy and security advice from a simulated household state."""
    resolved = await _simulated_household(
        request, current_user, session, requested_household_id=household_id
    )
    if resolved is None:
        return {"available": False, "recommendations": []}
    resolved_id, household_name = resolved
    state = await simulated_runtime_state(resolved_id, household_name)
    virtual_lights = [
        device for device in state["devices"] if device.get("virtual") and device["kind"] == "light"
    ]
    lights_on = [device for device in virtual_lights if device["state"] == "on"]
    recommendations: list[dict[str, str]] = []
    if lights_on:
        recommendations.append({
            "kind": "energy",
            "title": "Turn off unused virtual lighting",
            "detail": f"{len(lights_on)} simulated light(s) add {state['summary']['power_w']} W. Review whether their rooms have current motion.",
        })
    else:
        recommendations.append({
            "kind": "energy",
            "title": "Lighting energy is at the simulated baseline",
            "detail": (
                "No virtual lights are on; use the recorded appliance-power channels "
                "and room activity when reviewing energy."
            ),
        })
    active_rooms = [room["name"] for room in state["rooms"] if room["occupied"]]
    recommendations.append({
        "kind": "security",
        "title": "Review occupancy as inferred from ambient sensors",
        "detail": "Activity is currently inferred in " + ", ".join(active_rooms) + ". This is a dataset-calibrated simulated-live state, not a live alert.",
    })
    return {
        "available": True,
        "recommendations": recommendations,
        "live_simulation": state.get("live_simulation"),
        "state": state,
    }


@router.get("/household-plan")
async def simulation_household_plan(
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
    household_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Build a forward-looking household plan from the simulated live state."""
    resolved = await _simulated_household(
        request, current_user, session, requested_household_id=household_id
    )
    if resolved is None:
        return {"available": False, "message": "Select a simulated household."}
    resolved_id, household_name = resolved
    state = await simulated_runtime_state(resolved_id, household_name)
    live = state.get("live_simulation", {})
    activity = str(live.get("activity") or "unknown activity")
    occupancy = str(state["summary"]["occupancy"])
    activity_context = (
        f"Current time-of-day context: {activity}."
        if household_name == CN_OBEE_NAME
        else f"Current inferred activity: {activity}."
    )
    if occupancy == "Away":
        next_context = "The resident is currently simulated as away; the next expected event is a return or entry transition."
        lighting = "Keep virtual lights off unless a simulated security-lighting rule is enabled."
    else:
        next_context = f"The next 10-minute simulation update will continue or transition from {activity}."
        lighting = "Keep virtual lights aligned with activity in occupied rooms; turn them off when the room becomes inactive."
    if household_name == CN_OBEE_NAME:
        plan_title = "CN-OBEE away plan" if occupancy == "Away" else "CN-OBEE current at-home plan"
        plan_summary = (
            "A source-backed CN-OBEE replay using recorded room occupancy, environment, "
            "window state, and appliance power. EcoNest actions affect virtual devices only."
        )
        expected_pattern = "CN-OBEE room occupancy and appliance-power pattern."
    else:
        if occupancy == "Away":
            plan_title = "CASAS away plan"
        elif activity == "Work":
            plan_title = "CASAS current at-home work plan"
        else:
            plan_title = "CASAS current at-home plan"
        plan_summary = (
            "A synthetic 2026 plan calibrated from CASAS Aruba activity patterns. "
            "It controls only EcoNest virtual devices."
        )
        expected_pattern = "CASAS-calibrated room transitions and short motion bursts."
    return {
        "available": True,
        "context": {
            "title": "Upcoming household context",
            "items": [
                f"Simulated now: {live.get('clock', 'unknown time')}.",
                activity_context,
                next_context,
            ],
        },
        "plan": {
            "title": plan_title,
            "summary": plan_summary,
            "settings": [
                {"label": "Comfort temperature", "recommendation": f"Maintain the simulated indoor temperature near {state['summary']['indoor_temperature_f']}°F."},
                {"label": "Virtual lighting", "recommendation": lighting},
                {"label": "Security", "recommendation": "Interpret motion and door contacts as simulated occupancy evidence; require confirmation before virtual actions."},
            ],
            "expected_patterns": [
                f"{activity} sensor pattern while the simulated household is {occupancy.lower()}.",
                expected_pattern,
            ],
            "watch_for": [
                "Motion or door activity that conflicts with the current simulated occupancy state.",
                "Virtual lighting left on after the corresponding room becomes inactive.",
            ],
        },
    }


@router.post("/prompt")
async def simulation_prompt(
    payload: SimulationPromptRequest,
    request: Request,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Interpret a bounded household prompt without calling real-home services."""
    resolved = await _simulated_household(
        request, current_user, session, requested_household_id=payload.household_id
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Select a simulated household")
    household_id, household_name = resolved
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a prompt")
    normalized = prompt.lower()
    if "energy" in normalized:
        recommendations = await simulation_recommendations(
            request, current_user, session, household_id
        )
        return {"kind": "recommendation", "message": "Energy recommendations are ready.", **recommendations}
    if "security" in normalized or "safe" in normalized:
        recommendations = await simulation_recommendations(
            request, current_user, session, household_id
        )
        security = [item for item in recommendations["recommendations"] if item["kind"] == "security"]
        return {"kind": "recommendation", "message": "Security recommendation is ready.", "recommendations": security}
    light_room = "living" if "living" in normalized else "kitchen" if "kitchen" in normalized else None
    wants_on = "turn on" in normalized or "switch on" in normalized
    wants_off = "turn off" in normalized or "switch off" in normalized
    if light_room and (wants_on or wants_off):
        action = f"{light_room}_light_{'on' if wants_on else 'off'}"
        return {
            "kind": "confirmation",
            "message": (
                f"I can turn {'on' if wants_on else 'off'} the virtual {light_room}-room "
                f"light in {household_name}."
            ),
            "action": action,
            "household_id": household_id,
        }
    state = await simulated_runtime_state(household_id, household_name)
    live = state.get("live_simulation", {})
    return {
        "kind": "answer",
        "message": (
            f"The simulated home is {state['summary']['occupancy'].lower()}. "
            f"Current simulated activity: {live.get('activity', 'unknown')}; "
            f"active rooms: {state['summary']['active_rooms']}."
        ),
        "state": state,
    }


def _unavailable_state() -> dict[str, Any]:
    """Return a safe empty state rather than falling back to live-home data."""
    return {
        "available": False,
        "message": "Select a supported simulated household in the household switcher to explore its digital twin.",
    }


async def _simulated_household(
    request: Request,
    current_user: UserProfile,
    session: AsyncSession,
    *,
    requested_household_id: int | None = None,
) -> tuple[int, str] | None:
    """Resolve an active supported simulation after checking access."""
    household_id = requested_household_id
    if household_id is None:
        requested = request.cookies.get(ACTIVE_HOUSEHOLD_COOKIE_NAME)
        try:
            household_id = (
                int(requested) if requested is not None else current_user.household_id
            )
        except ValueError:
            household_id = current_user.household_id
    if household_id is None:
        return None
    if not has_permission(current_user.role, USER_ADMIN) and household_id != current_user.household_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    result = await session.execute(
        text("SELECT id, name FROM households WHERE id = :id"),
        {"id": household_id},
    )
    household = result.mappings().first()
    if household is None or household["name"] not in {
        CITY_APARTMENT_NAME,
        CASAS_ARUBA_NAME,
        CN_OBEE_NAME,
    }:
        return None
    return int(household["id"]), str(household["name"])
