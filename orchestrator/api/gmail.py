"""Read-only Gmail metadata routes for household context."""

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
from orchestrator.config import Settings, get_settings
from orchestrator.core.audit import write_audit_event_async
from orchestrator.core.database import get_mysql_session
from orchestrator.core.google_gmail import (
    GMAIL_METADATA_SCOPE,
    GMAIL_READONLY_SCOPE,
    current_gmail_context,
    gmail_connection_status,
    save_gmail_connection,
    sync_gmail_context,
)

router = APIRouter(prefix="/integrations/gmail", tags=["integrations"])
settings = get_settings()
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_LOCAL_DEMO_USER_ID = 0


class GmailStatusResponse(BaseModel):
    """Non-sensitive state for the Gmail connector page."""

    enabled: bool
    configured: bool
    connected: bool
    connected_at: str | None = None
    updated_at: str | None = None
    local_setup_available: bool = False
    local_demo_mode: bool
    requires_sign_in: bool
    read_body_for_context: bool
    body_context_authorized: bool = False
    allowed_label_ids: list[str]
    sender_allowlist_configured: bool


class GmailSetupRequest(BaseModel):
    """Local demo proof required to start the Gmail OAuth flow."""

    passphrase: str = Field(min_length=1, max_length=256)


@router.get("", response_class=HTMLResponse)
async def gmail_page() -> HTMLResponse:
    """Serve the Gmail connector page."""
    page = Path(__file__).resolve().parents[1] / "static" / "gmail.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/status", response_model=GmailStatusResponse)
async def gmail_status(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> GmailStatusResponse:
    """Return Gmail connection state without message data."""
    configured = _is_configured(settings)
    owner_id = _owner_id(current_user)
    connection: dict[str, Any] = {"connected": False}
    if configured and owner_id is not None:
        connection = await gmail_connection_status(session, owner_id)
    return GmailStatusResponse(
        enabled=settings.GMAIL_ENABLED,
        configured=configured,
        local_setup_available=_local_setup_available(settings, current_user),
        local_demo_mode=not settings.COMMAND_CENTER_AUTH_REQUIRED,
        requires_sign_in=current_user is None and settings.COMMAND_CENTER_AUTH_REQUIRED,
        read_body_for_context=settings.GMAIL_READ_BODY_FOR_CONTEXT,
        allowed_label_ids=_label_ids(settings),
        sender_allowlist_configured=bool(_sender_domains(settings)),
        **connection,
    )


@router.get("/connect")
async def gmail_connect(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> RedirectResponse:
    """Start authenticated Gmail OAuth with metadata-only scope."""
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in before connecting Gmail")
    _require_configuration(settings)
    return RedirectResponse(_authorization_url(current_user, settings), status_code=307)


@router.post("/authorize")
async def gmail_authorize(
    request: GmailSetupRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, str]:
    """Create a local-demo Gmail OAuth URL after a local setup proof."""
    _require_configuration(settings)
    actor = current_user
    if actor is None:
        _validate_local_setup_passphrase(request.passphrase, settings)
        existing = await gmail_connection_status(session, _LOCAL_DEMO_USER_ID)
        if existing["connected"]:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A local demo Gmail account is already connected")
        actor = _local_demo_actor()
    return {"authorization_url": _authorization_url(actor, settings)}


@router.post("/reauthorize")
async def gmail_reauthorize(
    request: GmailSetupRequest,
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
) -> dict[str, str]:
    """Start a new consent flow when the configured Gmail scope changes."""
    _require_configuration(settings)
    actor = current_user
    if actor is None:
        _validate_local_setup_passphrase(request.passphrase, settings)
        actor = _local_demo_actor()
    return {"authorization_url": _authorization_url(actor, settings)}


@router.get("/context")
async def gmail_context(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Return derived Gmail context, never raw message content."""
    owner_id = _required_owner_id(current_user)
    return await current_gmail_context(session, owner_id)


@router.post("/context/refresh")
async def refresh_gmail_context(
    current_user: Annotated[UserProfile | None, Depends(get_optional_current_user)],
    session: AsyncSession = Depends(get_mysql_session),
) -> dict[str, Any]:
    """Synchronize selected Gmail metadata and return derived categories."""
    owner_id = _required_owner_id(current_user)
    result = await sync_gmail_context(session, settings, owner_id)
    if not result["synced"]:
        raise HTTPException(status_code=503, detail=result["reason"])
    context = await current_gmail_context(session, owner_id)
    await write_audit_event_async(
        "gmail.context_synced",
        {"source": "gmail", "user_id": owner_id, "context_notices": result["context_notices"], "success": True},
    )
    return {**context, **result}


@router.get("/callback")
async def gmail_callback(
    state_token: str = Query(alias="state"),
    code: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_mysql_session),
) -> RedirectResponse:
    """Exchange a Gmail OAuth code and save encrypted credentials."""
    payload = _decode_state(state_token, settings)
    if error is not None:
        await write_audit_event_async("gmail.connection.cancelled", {"source": "google_oauth"})
        return RedirectResponse("/integrations?gmail=cancelled", status_code=303)
    if not code:
        return RedirectResponse("/integrations?gmail=failed", status_code=303)
    tokens = await _exchange_code(code, settings)
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=502, detail="Google did not return a refresh token")
    user_id = _optional_int(payload.get("sub"))
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid Gmail OAuth owner")
    await save_gmail_connection(
        session,
        user_id=user_id,
        refresh_token=refresh_token,
        access_token=tokens.get("access_token") if isinstance(tokens.get("access_token"), str) else None,
        expires_at=_expires_at(tokens.get("expires_in")),
        scopes=str(tokens.get("scope") or _gmail_scope(settings)),
        settings=settings,
    )
    await write_audit_event_async("gmail.connected", {"source": "google_oauth", "user_id": user_id, "success": True})
    return RedirectResponse("/integrations?gmail=connected", status_code=303)


def _authorization_url(actor: UserProfile, config: Settings) -> str:
    parameters = {
        "client_id": _client_id(config),
        "redirect_uri": config.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": _gmail_scope(config),
        "access_type": "offline",
        "prompt": "consent",
        "state": _create_state(actor, config),
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(parameters)}"


def _is_configured(config: Settings) -> bool:
    return bool(config.GMAIL_ENABLED and _client_id(config) and _client_secret(config) and config.GMAIL_REDIRECT_URI)


def _gmail_scope(config: Settings) -> str:
    """Return the smallest Gmail OAuth scope allowed by the selected context mode."""
    return GMAIL_READONLY_SCOPE if config.GMAIL_READ_BODY_FOR_CONTEXT else GMAIL_METADATA_SCOPE


def _require_configuration(config: Settings) -> None:
    if not _is_configured(config):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Gmail is not configured")


def _owner_id(current_user: UserProfile | None) -> int | None:
    if current_user is not None:
        return current_user.id
    return _LOCAL_DEMO_USER_ID if not settings.COMMAND_CENTER_AUTH_REQUIRED else None


def _required_owner_id(current_user: UserProfile | None) -> int:
    owner_id = _owner_id(current_user)
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to view Gmail context")
    return owner_id


def _local_setup_available(config: Settings, current_user: UserProfile | None) -> bool:
    return bool(current_user is None and not config.COMMAND_CENTER_AUTH_REQUIRED and config.GMAIL_SETUP_PASSPHRASE)


def _validate_local_setup_passphrase(passphrase: str, config: Settings) -> None:
    if not _local_setup_available(config, None) or not hmac.compare_digest(passphrase, config.GMAIL_SETUP_PASSPHRASE):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Gmail setup passphrase")


def _local_demo_actor() -> UserProfile:
    return UserProfile(id=_LOCAL_DEMO_USER_ID, email="local-gmail-demo@local", role="homeowner", household_id=None, is_active=True)


def _create_state(actor: UserProfile, config: Settings) -> str:
    now = datetime.now(UTC)
    return str(jwt.encode({"sub": str(actor.id), "type": "gmail_oauth", "iat": now, "exp": now + timedelta(minutes=10)}, config.SECRET_KEY, algorithm=config.ALGORITHM))


def _decode_state(state_token: str, config: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(state_token, config.SECRET_KEY, algorithms=[config.ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid Gmail OAuth state") from exc
    if not isinstance(payload, dict) or payload.get("type") != "gmail_oauth":
        raise HTTPException(status_code=400, detail="Invalid Gmail OAuth state")
    return payload


async def _exchange_code(code: str, config: Settings) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data={"code": code, "client_id": _client_id(config), "client_secret": _client_secret(config), "redirect_uri": config.GMAIL_REDIRECT_URI, "grant_type": "authorization_code"})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Gmail authorization failed") from exc
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Google returned an invalid token response")
    return payload


def _expires_at(value: Any) -> datetime | None:
    try:
        return datetime.now(UTC) + timedelta(seconds=int(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _client_id(config: Settings) -> str:
    return config.GMAIL_CLIENT_ID or config.GOOGLE_CALENDAR_CLIENT_ID


def _client_secret(config: Settings) -> str:
    return config.GMAIL_CLIENT_SECRET or config.GOOGLE_CALENDAR_CLIENT_SECRET


def _label_ids(config: Settings) -> list[str]:
    return [value.strip() for value in config.GMAIL_ALLOWED_LABEL_IDS.split(",") if value.strip()] or ["INBOX"]


def _sender_domains(config: Settings) -> list[str]:
    return [value.strip().lower().lstrip("@") for value in config.GMAIL_ALLOWED_SENDER_DOMAINS.split(",") if value.strip()]
