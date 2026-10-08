"""Google Calendar read-only connection routes for household context."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.api.auth import UserProfile, get_optional_current_user
from orchestrator.api.autonomy import submit_calendar_preview_reviews
from orchestrator.config import Settings, get_settings
from orchestrator.core.audit import write_audit_event_async
from orchestrator.core.database import get_mysql_session
from orchestrator.core.google_calendar import (
    calendar_connection_status,
    current_calendar_context,
    save_calendar_connection,
    sync_calendar_context,
    upcoming_calendar_events,
)

router = APIRouter(prefix="/integrations/google-calendar", tags=["integrations"])
settings = get_settings()
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"


class CalendarStatusResponse(BaseModel):
    """A non-sensitive Google Calendar connection status."""

    enabled: bool
    configured: bool
    connected: bool
    calendar_id: str | None = None
    connected_at: str | None = None
    updated_at: str | None = None
    sync_lookahead_days: int
    local_setup_available: bool = False


class CalendarSetupRequest(BaseModel):
    """A local proof required to begin the demo Calendar OAuth flow."""

    passphrase: str = Field(min_length=1, max_length=256)


def _calendar_owner_id(current_user: UserProfile | None) -> int | None:
    """Select the authenticated user or the one local demo calendar owner."""
    if current_user is not None:
        return current_user.id
    if not settings.COMMAND_CENTER_AUTH_REQUIRED:
        return _LOCAL_DEMO_USER_ID
    return None


@router.get("", response_class=HTMLResponse)
async def google_calendar_page() -> HTMLResponse:
    """Serve the Google Calendar integration setup page."""
    page = Path(__file__).resolve().parents[1] / "static" / "google-calendar.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/status", response_model=CalendarStatusResponse)
async def google_calendar_status(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> CalendarStatusResponse:
    """Return connection state without revealing event or credential data."""
    configured = _is_configured(settings)
    connection: dict[str, Any] = {"connected": False}
    local_setup_available = _local_setup_available(settings, current_user)
    if configured and current_user is not None:
        connection = await calendar_connection_status(session, current_user.id)
    elif configured and not settings.COMMAND_CENTER_AUTH_REQUIRED:
        connection = await calendar_connection_status(session, _LOCAL_DEMO_USER_ID)
    return CalendarStatusResponse(
        enabled=settings.GOOGLE_CALENDAR_ENABLED,
        configured=configured,
        sync_lookahead_days=settings.GOOGLE_CALENDAR_SYNC_LOOKAHEAD_DAYS,
        local_setup_available=local_setup_available and not connection["connected"],
        **connection,
    )


@router.get("/connect")
async def google_calendar_connect(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> RedirectResponse:
    """Begin Google OAuth with the minimal read-only events scope."""
    actor = _require_actor(current_user)
    _require_configuration(settings)
    state = _create_state(actor, settings)
    parameters = {
        "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_EVENTS_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(parameters)}", status_code=307)


@router.post("/authorize")
async def google_calendar_authorize(
    request: CalendarSetupRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, str]:
    """Create an OAuth URL after a local-demo setup proof."""
    _require_configuration(settings)
    actor = current_user
    if actor is None:
        _validate_local_setup_passphrase(request.passphrase, settings)
        existing = await calendar_connection_status(session, _LOCAL_DEMO_USER_ID)
        if existing["connected"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A local demo calendar is already connected",
            )
        actor = _local_demo_actor()
    state = _create_state(actor, settings)
    parameters = {
        "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_EVENTS_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"authorization_url": f"{GOOGLE_AUTH_URL}?{urlencode(parameters)}"}


@router.get("/context")
async def google_calendar_context(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Return derived calendar context without returning calendar event text."""
    owner_id = _calendar_owner_id(current_user)
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to view calendar context")
    return await current_calendar_context(session, owner_id)


@router.get("/events")
async def google_calendar_events(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Read upcoming event titles for the owner interface without retaining them."""
    owner_id = _calendar_owner_id(current_user)
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to view calendar events")
    events = await upcoming_calendar_events(session, settings, owner_id)
    return {"events": events, "retention": "Displayed on demand only; event titles are not stored or sent to the model."}


@router.post("/context/refresh")
async def refresh_google_calendar_context(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Synchronize the short upcoming window and return its derived context."""
    owner_id = _calendar_owner_id(current_user)
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to refresh calendar context")
    result = await sync_calendar_context(session, settings, owner_id)
    if not result["synced"]:
        raise HTTPException(status_code=503, detail=result["reason"])
    context = await current_calendar_context(session, owner_id)
    await write_audit_event_async(
        "calendar.google.context_synced",
        {"source": "google_calendar", "user_id": owner_id, "context_windows": result["context_windows"], "success": True},
    )
    return {**context, **result}


@router.post("/context/review")
async def review_calendar_context(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Queue an advisory energy/security preview for active or upcoming context."""
    owner_id = _calendar_owner_id(current_user)
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to review calendar context")
    context = await current_calendar_context(session, owner_id)
    selected = next(
        iter(context.get("active_contexts") or context.get("upcoming_contexts") or []),
        None,
    )
    if not isinstance(selected, dict):
        raise HTTPException(status_code=409, detail="No upcoming hosting or away calendar context is available to review")
    reviews = await submit_calendar_preview_reviews(selected)
    if not reviews:
        raise HTTPException(status_code=409, detail="This calendar context does not require an advisory review")
    await write_audit_event_async(
        "calendar.google.review_requested",
        {"source": "calendar_context", "mode": selected.get("mode"), "review_count": len(reviews), "success": True},
    )
    return {"reviews": reviews, "mode": selected.get("mode"), "message": "Calendar-aware advisory reviews were queued. No device action will be attempted."}


@router.get("/callback")
async def google_calendar_callback(
    state_token: str = Query(alias="state"),
    code: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_mysql_session),
) -> RedirectResponse:
    """Exchange a Google authorization code and save encrypted credentials."""
    payload = _decode_state(state_token, settings)
    if error is not None:
        await write_audit_event_async("calendar.google.connection.cancelled", {"source": "google_oauth"})
        return RedirectResponse("/integrations/google-calendar?status=cancelled", status_code=303)
    if not code:
        return RedirectResponse("/integrations/google-calendar?status=failed", status_code=303)
    tokens = await _exchange_code(code, settings)
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=502, detail="Google did not return a refresh token")
    expires_at = _expires_at(tokens.get("expires_in"))
    await save_calendar_connection(
        session,
        user_id=_optional_int(payload.get("sub")),
        household_id=_optional_int(payload.get("household_id")),
        refresh_token=refresh_token,
        access_token=tokens.get("access_token") if isinstance(tokens.get("access_token"), str) else None,
        expires_at=expires_at,
        scopes=str(tokens.get("scope") or GOOGLE_EVENTS_SCOPE),
        settings=settings,
    )
    await write_audit_event_async(
        "calendar.google.connected",
        {"source": "google_oauth", "user_id": payload.get("sub"), "success": True},
    )
    return RedirectResponse("/integrations/google-calendar?status=connected", status_code=303)


def _is_configured(config: Settings) -> bool:
    """Return whether all required Google OAuth settings are present."""
    return bool(
        config.GOOGLE_CALENDAR_ENABLED
        and config.GOOGLE_CALENDAR_CLIENT_ID
        and config.GOOGLE_CALENDAR_CLIENT_SECRET
        and config.GOOGLE_CALENDAR_REDIRECT_URI
    )


def _require_configuration(config: Settings) -> None:
    """Fail closed until a server-side Google OAuth client is configured."""
    if not _is_configured(config):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Calendar is not configured")


def _require_actor(current_user: UserProfile | None) -> UserProfile:
    """Require an authenticated household member before linking a calendar."""
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in before connecting Google Calendar")
    return current_user


_LOCAL_DEMO_USER_ID = 0


def _local_setup_available(config: Settings, current_user: UserProfile | None) -> bool:
    """Allow a passphrase only for the explicitly unauthenticated local demo."""
    return bool(
        current_user is None
        and not config.COMMAND_CENTER_AUTH_REQUIRED
        and config.GOOGLE_CALENDAR_SETUP_PASSPHRASE
    )


def _validate_local_setup_passphrase(passphrase: str, config: Settings) -> None:
    """Validate local setup proof without leaking comparison details."""
    expected = config.GOOGLE_CALENDAR_SETUP_PASSPHRASE
    if not _local_setup_available(config, None) or not hmac.compare_digest(
        passphrase, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Calendar setup passphrase",
        )


def _local_demo_actor() -> UserProfile:
    """Return the single synthetic owner used only for local demo pairing."""
    return UserProfile(
        id=_LOCAL_DEMO_USER_ID,
        email="local-calendar-demo@local",
        role="homeowner",
        household_id=None,
        is_active=True,
    )


def _create_state(actor: UserProfile, config: Settings) -> str:
    """Create a short-lived signed OAuth state binding the callback to its user."""
    now = datetime.now(UTC)
    return str(
        jwt.encode(
            {
                "sub": str(actor.id),
                "household_id": actor.household_id,
                "type": "google_calendar_oauth",
                "iat": now,
                "exp": now + timedelta(minutes=10),
            },
            config.SECRET_KEY,
            algorithm=config.ALGORITHM,
        )
    )


def _decode_state(state_token: str, config: Settings) -> dict[str, Any]:
    """Validate the signed, short-lived OAuth callback state."""
    try:
        payload = jwt.decode(state_token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid Google OAuth state") from exc
    if not isinstance(payload, dict) or payload.get("type") != "google_calendar_oauth":
        raise HTTPException(status_code=400, detail="Invalid Google OAuth state")
    return payload


async def _exchange_code(code: str, config: Settings) -> dict[str, Any]:
    """Exchange an OAuth code server-side without exposing client credentials."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": config.GOOGLE_CALENDAR_CLIENT_ID,
                    "client_secret": config.GOOGLE_CALENDAR_CLIENT_SECRET,
                    "redirect_uri": config.GOOGLE_CALENDAR_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Google Calendar authorization failed") from exc
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Google returned an invalid token response")
    return payload


def _expires_at(value: Any) -> datetime | None:
    """Convert Google's optional lifetime to a UTC expiry timestamp."""
    try:
        return datetime.now(UTC) + timedelta(seconds=int(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    """Return an integer OAuth claim when it is present."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
