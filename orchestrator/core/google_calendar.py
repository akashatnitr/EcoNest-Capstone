"""Secure persistence helpers for EcoNest's read-only Google Calendar link."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings

GOOGLE_CALENDAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS google_calendar_connections (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NULL,
    household_id INT NULL,
    calendar_id VARCHAR(255) NOT NULL DEFAULT 'primary',
    encrypted_refresh_token TEXT NOT NULL,
    encrypted_access_token TEXT NULL,
    access_token_expires_at TIMESTAMP NULL,
    scopes VARCHAR(1024) NOT NULL,
    connected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP NULL,
    UNIQUE KEY unique_google_calendar_connection (user_id, calendar_id),
    INDEX idx_google_calendar_household (household_id)
) ENGINE=InnoDB
"""

CALENDAR_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_context_windows (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    source_event_hash CHAR(64) NOT NULL,
    context_mode VARCHAR(32) NOT NULL,
    starts_at TIMESTAMP NOT NULL,
    ends_at TIMESTAMP NOT NULL,
    confidence VARCHAR(16) NOT NULL,
    guidance JSON NOT NULL,
    synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_calendar_context_event (user_id, source_event_hash),
    INDEX idx_calendar_context_active (user_id, starts_at, ends_at)
) ENGINE=InnoDB
"""
CALENDAR_CONTEXT_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_context_review_claims (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    source_event_hash CHAR(64) NOT NULL,
    claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_calendar_context_review (user_id, source_event_hash)
) ENGINE=InnoDB
"""
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_AWAY_TERMS = ("trip", "vacation", "travel", "away", "out of town")
_HOSTING_TERMS = ("party", "guests", "gathering", "celebration", "hosting")


async def ensure_google_calendar_schema(session: AsyncSession) -> None:
    """Create read-only Google Calendar credential storage."""
    await session.execute(text(GOOGLE_CALENDAR_SCHEMA))
    await session.execute(text(CALENDAR_CONTEXT_SCHEMA))
    await session.execute(text(CALENDAR_CONTEXT_REVIEW_SCHEMA))
    await session.commit()


def encrypt_calendar_secret(value: str, settings: Settings) -> str:
    """Encrypt an OAuth credential before persistence; never log its value."""
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_calendar_secret(value: str, settings: Settings) -> str | None:
    """Decrypt a stored OAuth credential, returning no value if it is invalid."""
    try:
        return _fernet(settings).decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


async def save_calendar_connection(
    session: AsyncSession,
    *,
    user_id: int | None,
    household_id: int | None,
    refresh_token: str,
    access_token: str | None,
    expires_at: datetime | None,
    scopes: str,
    settings: Settings,
) -> None:
    """Store only encrypted Google OAuth credentials for the selected calendar."""
    await session.execute(
        text(
            """
            INSERT INTO google_calendar_connections
              (user_id, household_id, calendar_id, encrypted_refresh_token,
               encrypted_access_token, access_token_expires_at, scopes, revoked_at)
            VALUES
              (:user_id, :household_id, 'primary', :refresh_token,
               :access_token, :expires_at, :scopes, NULL)
            ON DUPLICATE KEY UPDATE
              household_id = VALUES(household_id),
              encrypted_refresh_token = VALUES(encrypted_refresh_token),
              encrypted_access_token = VALUES(encrypted_access_token),
              access_token_expires_at = VALUES(access_token_expires_at),
              scopes = VALUES(scopes), revoked_at = NULL
            """
        ),
        {
            "user_id": user_id,
            "household_id": household_id,
            "refresh_token": encrypt_calendar_secret(refresh_token, settings),
            "access_token": (
                encrypt_calendar_secret(access_token, settings) if access_token else None
            ),
            "expires_at": expires_at,
            "scopes": scopes[:1024],
        },
    )
    await session.commit()


async def calendar_connection_status(
    session: AsyncSession,
    user_id: int | None,
) -> dict[str, Any]:
    """Return non-sensitive connection state for a user."""
    result = await session.execute(
        text(
            """
            SELECT calendar_id, connected_at, updated_at, revoked_at
            FROM google_calendar_connections
            WHERE user_id <=> :user_id AND revoked_at IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        return {"connected": False}
    return {
        "connected": True,
        "calendar_id": row["calendar_id"],
        "connected_at": _isoformat(row["connected_at"]),
        "updated_at": _isoformat(row["updated_at"]),
    }


async def sync_calendar_context(
    session: AsyncSession, settings: Settings, user_id: int
) -> dict[str, Any]:
    """Fetch upcoming events and persist only derived, non-identifying context."""
    connection = await _calendar_connection(session, user_id)
    if connection is None:
        return {"synced": False, "reason": "Google Calendar is not connected"}
    access_token = await _access_token(session, connection, settings)
    now = _utc_naive_now()
    request_now = now.replace(tzinfo=UTC)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                GOOGLE_EVENTS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "timeMin": request_now.isoformat(),
                    "timeMax": (
                        request_now
                        + timedelta(days=settings.GOOGLE_CALENDAR_SYNC_LOOKAHEAD_DAYS)
                    ).isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 100,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return {"synced": False, "reason": "Google Calendar events are unavailable"}
    payload = response.json()
    events = payload.get("items") if isinstance(payload, dict) else []
    retained = 0
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        derived = _derive_context_window(event)
        if derived is None:
            continue
        await session.execute(
            text(
                """
                INSERT INTO calendar_context_windows
                  (user_id, source_event_hash, context_mode, starts_at, ends_at, confidence, guidance)
                VALUES (:user_id, :event_hash, :mode, :starts_at, :ends_at, :confidence, :guidance)
                ON DUPLICATE KEY UPDATE context_mode = VALUES(context_mode),
                  starts_at = VALUES(starts_at), ends_at = VALUES(ends_at),
                  confidence = VALUES(confidence), guidance = VALUES(guidance)
                """
            ),
            {"user_id": user_id, **derived, "guidance": json.dumps(derived["guidance"])},
        )
        retained += 1
    await session.execute(
        text("DELETE FROM calendar_context_windows WHERE user_id = :user_id AND ends_at < :now"),
        {"user_id": user_id, "now": now},
    )
    await session.commit()
    return {"synced": True, "context_windows": retained}


async def upcoming_calendar_events(
    session: AsyncSession, settings: Settings, user_id: int
) -> list[dict[str, str]]:
    """Return upcoming event titles for the owner's UI without persisting them."""
    connection = await _calendar_connection(session, user_id)
    if connection is None:
        return []
    access_token = await _access_token(session, connection, settings)
    now = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                GOOGLE_EVENTS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "timeMin": now.isoformat(),
                    "timeMax": (now + timedelta(days=settings.GOOGLE_CALENDAR_SYNC_LOOKAHEAD_DAYS)).isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 12,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else []
    return [
        {
            "title": str(event.get("summary") or "Untitled calendar event")[:160],
            "starts_at": _isoformat(_event_time(event.get("start"))) or "",
            "ends_at": _isoformat(_event_time(event.get("end"))) or "",
        }
        for event in items if isinstance(event, dict)
    ]


async def current_calendar_context(
    session: AsyncSession, user_id: int
) -> dict[str, Any]:
    """Return bounded active and upcoming home context, never calendar text."""
    now = _utc_naive_now()
    result = await session.execute(
        text(
            """
            SELECT context_mode, starts_at, ends_at, confidence, guidance
            FROM calendar_context_windows
            WHERE user_id = :user_id AND ends_at >= :now
            ORDER BY starts_at ASC LIMIT 8
            """
        ),
        {"user_id": user_id, "now": now},
    )
    windows = [_context_view(row) for row in result.mappings().all()]
    current_time = now.replace(tzinfo=UTC).isoformat()
    active = [item for item in windows if item["starts_at"] <= current_time <= item["ends_at"]]
    current_mode = active[0]["mode"] if active else "normal"
    return {
        "available": True,
        "current_mode": current_mode,
        "active_contexts": active,
        "upcoming_contexts": windows[:3],
        "guidance": _mode_guidance(current_mode),
        "privacy": "Derived calendar context only; event titles, descriptions, attendees, and locations are not retained.",
    }


async def connected_calendar_user_ids(session: AsyncSession) -> list[int]:
    """Return linked calendar owners for the periodic context monitor."""
    result = await session.execute(
        text(
            """
            SELECT DISTINCT user_id
            FROM google_calendar_connections
            WHERE user_id IS NOT NULL AND revoked_at IS NULL
            """
        )
    )
    return [int(row[0]) for row in result.all()]


async def claim_active_calendar_context_reviews(
    session: AsyncSession, user_id: int
) -> list[dict[str, Any]]:
    """Claim each active context once so advisory reviews are never duplicated."""
    now = _utc_naive_now()
    result = await session.execute(
        text(
            """
            SELECT source_event_hash, context_mode, starts_at, ends_at, confidence, guidance
            FROM calendar_context_windows
            WHERE user_id = :user_id AND starts_at <= :now AND ends_at >= :now
            ORDER BY starts_at ASC
            """
        ),
        {"user_id": user_id, "now": now},
    )
    claimed: list[dict[str, Any]] = []
    for row in result.mappings().all():
        insert = await session.execute(
            text(
                """
                INSERT IGNORE INTO calendar_context_review_claims (user_id, source_event_hash)
                VALUES (:user_id, :event_hash)
                """
            ),
            {"user_id": user_id, "event_hash": row["source_event_hash"]},
        )
        if insert.rowcount:
            claimed.append(_context_view(row))
    await session.commit()
    return claimed


async def _calendar_connection(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT encrypted_refresh_token, encrypted_access_token, access_token_expires_at
            FROM google_calendar_connections
            WHERE user_id = :user_id AND revoked_at IS NULL
            ORDER BY updated_at DESC LIMIT 1
            """
        ),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _access_token(
    session: AsyncSession, connection: dict[str, Any], settings: Settings
) -> str:
    access_token = decrypt_calendar_secret(str(connection.get("encrypted_access_token") or ""), settings)
    expires_at = connection.get("access_token_expires_at")
    if access_token and isinstance(expires_at, datetime) and expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=1):
        return access_token
    refresh_token = decrypt_calendar_secret(str(connection["encrypted_refresh_token"]), settings)
    if not refresh_token:
        raise RuntimeError("Google Calendar credential could not be decrypted")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.GOOGLE_CALENDAR_CLIENT_ID,
                "client_secret": settings.GOOGLE_CALENDAR_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Google did not return an access token")
    await session.execute(
        text("UPDATE google_calendar_connections SET encrypted_access_token = :token, access_token_expires_at = :expires_at WHERE encrypted_refresh_token = :refresh_token"),
        {"token": encrypt_calendar_secret(token, settings), "expires_at": datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600))), "refresh_token": connection["encrypted_refresh_token"]},
    )
    await session.commit()
    return token


def _derive_context_window(event: dict[str, Any]) -> dict[str, Any] | None:
    """Classify a relevant event transiently; do not retain its human text."""
    title = str(event.get("summary") or "").lower()
    mode = "away" if any(term in title for term in _AWAY_TERMS) else "hosting" if any(term in title for term in _HOSTING_TERMS) else ""
    start = _event_time(event.get("start"))
    end = _event_time(event.get("end"))
    event_id = str(event.get("id") or "")
    if not mode or start is None or end is None or not event_id:
        return None
    return {"event_hash": hashlib.sha256(event_id.encode("utf-8")).hexdigest(), "mode": mode, "starts_at": _as_utc_naive(start), "ends_at": _as_utc_naive(end), "confidence": "high", "guidance": _mode_guidance(mode)}


def _event_time(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("dateTime") or value.get("date")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _as_utc_naive(value: datetime) -> datetime:
    """Normalize external calendar time for timezone-naive MySQL timestamps."""
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_naive_now() -> datetime:
    """Return UTC in the same representation used by MySQL calendar windows."""
    return datetime.now(UTC).replace(tzinfo=None)


def _mode_guidance(mode: str) -> list[str]:
    if mode == "hosting":
        return ["Avoid comfort-reducing energy recommendations", "Expected activity does not suppress safety-critical alerts"]
    if mode == "away":
        return ["Favor energy-saving recommendations", "Elevate unexpected motion or door activity", "Keep safety monitoring active"]
    return ["Use normal household policies"]


def _context_view(row: Any) -> dict[str, Any]:
    guidance = row.get("guidance") if isinstance(row, dict) else row["guidance"]
    if isinstance(guidance, str):
        try:
            guidance = json.loads(guidance)
        except ValueError:
            guidance = []
    return {"mode": str(row["context_mode"]), "starts_at": _isoformat(row["starts_at"]), "ends_at": _isoformat(row["ends_at"]), "confidence": str(row["confidence"]), "guidance": guidance if isinstance(guidance, list) else []}


def _fernet(settings: Settings) -> Fernet:
    """Build a Fernet key from the dedicated key or the deployment secret."""
    configured_key = settings.GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY.strip()
    if configured_key:
        return Fernet(configured_key.encode("utf-8"))
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _isoformat(value: Any) -> str | None:
    """Serialize a database timestamp for the integration status response."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).isoformat()
    return None
