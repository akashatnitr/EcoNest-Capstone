"""Privacy-bounded Gmail metadata connector for household context."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings

GMAIL_CONNECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS gmail_connections (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    encrypted_access_token TEXT NULL,
    access_token_expires_at TIMESTAMP NULL,
    scopes VARCHAR(1024) NOT NULL,
    connected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP NULL,
    UNIQUE KEY unique_gmail_connection (user_id)
) ENGINE=InnoDB
"""

GMAIL_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS gmail_context_notices (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    source_message_hash CHAR(64) NOT NULL,
    context_category VARCHAR(32) NOT NULL,
    received_at TIMESTAMP NOT NULL,
    starts_at TIMESTAMP NULL,
    ends_at TIMESTAMP NULL,
    expires_at TIMESTAMP NOT NULL,
    confidence VARCHAR(16) NOT NULL,
    guidance JSON NOT NULL,
    synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_gmail_context_message (user_id, source_message_hash),
    INDEX idx_gmail_context_active (user_id, expires_at)
) ENGINE=InnoDB
"""

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_TRAVEL_TERMS = ("trip", "vacation", "travel", "flight", "hotel", "away")
_UTILITY_TERMS = ("utility", "electric bill", "power outage", "water service")


async def ensure_google_gmail_schema(session: AsyncSession) -> None:
    """Create storage for encrypted credentials and derived notice categories."""
    await session.execute(text(GMAIL_CONNECTION_SCHEMA))
    await session.execute(text(GMAIL_CONTEXT_SCHEMA))
    await _ensure_context_schedule_columns(session)
    await session.commit()


async def _ensure_context_schedule_columns(session: AsyncSession) -> None:
    """Add scheduled-window columns for deployments created before body context."""
    result = await session.execute(text("SHOW COLUMNS FROM gmail_context_notices"))
    columns = {str(row[0]) for row in result.all()}
    if "starts_at" not in columns:
        await session.execute(text("ALTER TABLE gmail_context_notices ADD COLUMN starts_at TIMESTAMP NULL AFTER received_at"))
    if "ends_at" not in columns:
        await session.execute(text("ALTER TABLE gmail_context_notices ADD COLUMN ends_at TIMESTAMP NULL AFTER starts_at"))


def encrypt_gmail_secret(value: str, settings: Settings) -> str:
    """Encrypt an OAuth credential before persistence."""
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_gmail_secret(value: str, settings: Settings) -> str | None:
    """Decrypt an OAuth credential without surfacing an invalid value."""
    try:
        return _fernet(settings).decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


async def save_gmail_connection(
    session: AsyncSession,
    *,
    user_id: int,
    refresh_token: str,
    access_token: str | None,
    expires_at: datetime | None,
    scopes: str,
    settings: Settings,
) -> None:
    """Store only encrypted Gmail OAuth credentials."""
    await session.execute(
        text(
            """
            INSERT INTO gmail_connections
              (user_id, encrypted_refresh_token, encrypted_access_token,
               access_token_expires_at, scopes, revoked_at)
            VALUES (:user_id, :refresh_token, :access_token, :expires_at, :scopes, NULL)
            ON DUPLICATE KEY UPDATE
              encrypted_refresh_token = VALUES(encrypted_refresh_token),
              encrypted_access_token = VALUES(encrypted_access_token),
              access_token_expires_at = VALUES(access_token_expires_at),
              scopes = VALUES(scopes), revoked_at = NULL
            """
        ),
        {
            "user_id": user_id,
            "refresh_token": encrypt_gmail_secret(refresh_token, settings),
            "access_token": encrypt_gmail_secret(access_token, settings) if access_token else None,
            "expires_at": expires_at,
            "scopes": scopes[:1024],
        },
    )
    await session.commit()


async def gmail_connection_status(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """Return non-sensitive Gmail connection state."""
    result = await session.execute(
        text(
            """
            SELECT connected_at, updated_at, scopes FROM gmail_connections
            WHERE user_id = :user_id AND revoked_at IS NULL
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
        "connected_at": _isoformat(row["connected_at"]),
        "updated_at": _isoformat(row["updated_at"]),
        "body_context_authorized": GMAIL_READONLY_SCOPE in str(row["scopes"] or "").split(),
    }


async def sync_gmail_context(
    session: AsyncSession, settings: Settings, user_id: int
) -> dict[str, Any]:
    """Reduce allowlisted Gmail metadata to short-lived context categories.

    This function asks Gmail for message metadata only. It never requests bodies,
    attachments, snippets, or message text, and persists no sender or subject.
    """
    domains = _allowed_sender_domains(settings)
    if not domains:
        return {"synced": False, "reason": "Configure GMAIL_ALLOWED_SENDER_DOMAINS before syncing"}
    connection = await _gmail_connection(session, user_id)
    if connection is None:
        return {"synced": False, "reason": "Gmail is not connected"}
    try:
        token = await _access_token(session, connection, settings)
        messages = await _context_messages(token, settings)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            return {
                "synced": False,
                "reason": "Gmail permission is unavailable; reauthorize Gmail to refresh context",
            }
        return {"synced": False, "reason": "Gmail metadata is unavailable"}
    except (httpx.HTTPError, RuntimeError, ValueError):
        return {"synced": False, "reason": "Gmail metadata is unavailable"}

    retained = 0
    for message in messages:
        derived = _derive_notice(message, domains, settings)
        if derived is None:
            continue
        await session.execute(
            text(
                """
                INSERT INTO gmail_context_notices
                  (user_id, source_message_hash, context_category, received_at,
                   starts_at, ends_at, expires_at, confidence, guidance)
                VALUES (:user_id, :message_hash, :category, :received_at,
                        :starts_at, :ends_at, :expires_at, :confidence, :guidance)
                ON DUPLICATE KEY UPDATE
                  context_category = VALUES(context_category),
                  received_at = VALUES(received_at), starts_at = VALUES(starts_at),
                  ends_at = VALUES(ends_at), expires_at = VALUES(expires_at),
                  confidence = VALUES(confidence), guidance = VALUES(guidance)
                """
            ),
            {"user_id": user_id, **derived, "guidance": json.dumps(derived["guidance"])},
        )
        retained += 1
    await session.execute(
        text("DELETE FROM gmail_context_notices WHERE user_id = :user_id AND expires_at < :now"),
        {"user_id": user_id, "now": _utc_naive_now()},
    )
    await session.commit()
    return {"synced": True, "context_notices": retained}


async def current_gmail_context(session: AsyncSession, user_id: int) -> dict[str, Any]:
    """Return only current derived notices, never raw Gmail metadata."""
    now = _utc_naive_now()
    result = await session.execute(
        text(
            """
            SELECT context_category, received_at, starts_at, ends_at, expires_at, confidence, guidance
            FROM gmail_context_notices
            WHERE user_id = :user_id AND expires_at >= :now
              AND (ends_at IS NULL OR ends_at >= :now)
            ORDER BY received_at DESC LIMIT 6
            """
        ),
        {"user_id": user_id, "now": now},
    )
    notices = [_notice_view(row) for row in result.mappings().all()]
    return {
        "available": True,
        "notices": notices,
        "guidance": _combine_guidance(notices),
        "privacy": "Gmail bodies, attachments, sender addresses, and subject text are never retained or supplied to the model.",
    }


async def upcoming_gmail_context_events(
    session: AsyncSession, user_id: int
) -> list[dict[str, str]]:
    """Return scheduled derived notices for owner-facing upcoming-context views.

    This intentionally presents a generated category label, never an email
    subject, sender, or body fragment.
    """
    now = _utc_naive_now()
    result = await session.execute(
        text(
            """
            SELECT context_category, starts_at, ends_at, confidence
            FROM gmail_context_notices
            WHERE user_id = :user_id AND starts_at IS NOT NULL AND ends_at >= :now
            ORDER BY starts_at ASC LIMIT 8
            """
        ),
        {"user_id": user_id, "now": now},
    )
    return [
        {
            "source": "gmail",
            "title": f"{str(row['context_category']).replace('_', ' ').title()} context (Gmail)",
            "starts_at": _isoformat(row["starts_at"]) or "",
            "ends_at": _isoformat(row["ends_at"]) or "",
            "confidence": str(row["confidence"]),
        }
        for row in result.mappings().all()
    ]


async def _context_messages(access_token: str, settings: Settings) -> list[dict[str, Any]]:
    """Read selected Gmail data needed for the explicitly configured context mode."""
    labels = _allowed_label_ids(settings)
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        listing = await client.get(
            GMAIL_MESSAGES_URL,
            headers=headers,
            params=[("labelIds", label) for label in labels] + [("maxResults", "50")],
        )
        listing.raise_for_status()
        payload = listing.json()
        references = payload.get("messages") if isinstance(payload, dict) else []
        messages: list[dict[str, Any]] = []
        for reference in references if isinstance(references, list) else []:
            message_id = reference.get("id") if isinstance(reference, dict) else None
            if not isinstance(message_id, str) or not message_id:
                continue
            parameters: list[tuple[str, str]] = [("format", "full" if settings.GMAIL_READ_BODY_FOR_CONTEXT else "metadata")]
            if not settings.GMAIL_READ_BODY_FOR_CONTEXT:
                parameters.extend([("metadataHeaders", "From"), ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")])
            response = await client.get(
                f"{GMAIL_MESSAGES_URL}/{message_id}",
                headers=headers,
                params=parameters,
            )
            response.raise_for_status()
            message = response.json()
            if isinstance(message, dict):
                messages.append(message)
    return messages


async def _gmail_connection(session: AsyncSession, user_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            """
            SELECT encrypted_refresh_token, encrypted_access_token, access_token_expires_at
            FROM gmail_connections
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
    token = decrypt_gmail_secret(str(connection.get("encrypted_access_token") or ""), settings)
    expires_at = connection.get("access_token_expires_at")
    if token and isinstance(expires_at, datetime) and expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=1):
        return token
    refresh_token = decrypt_gmail_secret(str(connection["encrypted_refresh_token"]), settings)
    if not refresh_token:
        raise RuntimeError("Gmail credential could not be decrypted")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": _client_id(settings),
                "client_secret": _client_secret(settings),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Google did not return a Gmail access token")
    await session.execute(
        text("UPDATE gmail_connections SET encrypted_access_token = :token, access_token_expires_at = :expires_at WHERE encrypted_refresh_token = :refresh_token"),
        {"token": encrypt_gmail_secret(access_token, settings), "expires_at": datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600))), "refresh_token": connection["encrypted_refresh_token"]},
    )
    await session.commit()
    return access_token


def _derive_notice(
    message: dict[str, Any], domains: set[str], settings: Settings
) -> dict[str, Any] | None:
    headers = _headers(message)
    sender = headers.get("from", "").lower()
    sender_domain = sender.rsplit("@", maxsplit=1)[-1].rstrip(">").strip()
    if sender_domain not in domains:
        return None
    content = " ".join([headers.get("subject", ""), _plain_text_body(message, settings)]).lower()
    category = "travel" if any(term in content for term in _TRAVEL_TERMS) else "utility" if any(term in content for term in _UTILITY_TERMS) else ""
    message_id = str(message.get("id") or "")
    if not category or not message_id:
        return None
    received_at = _message_time(message.get("internalDate"))
    starts_at, ends_at = _date_window(content, received_at)
    expires_at = max(ends_at or received_at, received_at + timedelta(days=settings.GMAIL_CONTEXT_RETENTION_DAYS))
    return {
        "message_hash": hashlib.sha256(message_id.encode("utf-8")).hexdigest(),
        "category": category,
        "received_at": received_at,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "expires_at": expires_at,
        "confidence": "medium",
        "guidance": _category_guidance(category, has_dates=starts_at is not None),
    }


def _headers(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload")
    values = payload.get("headers") if isinstance(payload, dict) else []
    return {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in values if isinstance(item, dict)
    }


def _plain_text_body(message: dict[str, Any], settings: Settings) -> str:
    """Decode a bounded plain-text body only for the explicit test-body mode."""
    if not settings.GMAIL_READ_BODY_FOR_CONTEXT:
        return ""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    return _part_text(payload)[:8000]


def _part_text(part: dict[str, Any]) -> str:
    mime_type = str(part.get("mimeType") or "")
    body = part.get("body")
    encoded = body.get("data") if isinstance(body, dict) else None
    text_value = ""
    if mime_type.startswith("text/plain") and isinstance(encoded, str):
        try:
            text_value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            text_value = ""
    children = part.get("parts")
    if isinstance(children, list):
        text_value += " ".join(_part_text(child) for child in children if isinstance(child, dict))
    return text_value


def _date_window(content: str, received_at: datetime) -> tuple[datetime | None, datetime | None]:
    """Derive explicit date values from a test email without persisting its text."""
    dates: list[datetime] = []
    for raw in re.findall(r"\b\d{4}-\d{1,2}-\d{1,2}\b", content):
        try:
            dates.append(datetime.strptime(raw, "%Y-%m-%d"))
        except ValueError:
            continue
    months = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    month_numbers = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
        "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    for month, day, year in re.findall(rf"\b({months})\.?\s+(\d{{1,2}})(?:,?\s*(\d{{4}}))?", content):
        try:
            parsed = datetime(int(year or received_at.year), month_numbers[month.lower()], int(day))
            dates.append(parsed)
        except ValueError:
            continue
    for month, start_day, end_day, year in re.findall(
        rf"\b({months})\.?\s+(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})(?:,?\s*(\d{{4}}))?",
        content,
    ):
        try:
            month_number = month_numbers[month.lower()]
            event_year = int(year or received_at.year)
            dates.extend(
                [
                    datetime(event_year, month_number, int(start_day)),
                    datetime(event_year, month_number, int(end_day)),
                ]
            )
        except ValueError:
            continue
    unique_dates = sorted(set(dates))
    if not unique_dates:
        return None, None
    starts_at = unique_dates[0]
    ends_at = (unique_dates[1] if len(unique_dates) > 1 else starts_at) + timedelta(days=1)
    return starts_at, ends_at


def _message_time(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return _utc_naive_now()


def _category_guidance(category: str, *, has_dates: bool = False) -> list[str]:
    if category == "travel":
        date_guidance = "Use the derived trip window as advisory timing" if has_dates else "No trip dates were derived from this email"
        return ["Treat travel notice as advisory context", "Consider energy-saving and security recommendations", date_guidance]
    return ["Treat utility notice as advisory context", "Keep safety monitoring active", "Do not act on email text"]


def _combine_guidance(notices: list[dict[str, Any]]) -> list[str]:
    guidance: list[str] = []
    for notice in notices:
        for item in notice.get("guidance", []):
            if isinstance(item, str) and item not in guidance:
                guidance.append(item)
    return guidance[:5] or ["No active Gmail-derived household context"]


def _notice_view(row: Any) -> dict[str, Any]:
    guidance = row["guidance"]
    if isinstance(guidance, str):
        try:
            guidance = json.loads(guidance)
        except ValueError:
            guidance = []
    return {
        "category": str(row["context_category"]),
        "received_at": _isoformat(row["received_at"]),
        "starts_at": _isoformat(row["starts_at"]),
        "ends_at": _isoformat(row["ends_at"]),
        "expires_at": _isoformat(row["expires_at"]),
        "confidence": str(row["confidence"]),
        "guidance": guidance if isinstance(guidance, list) else [],
    }


def _allowed_sender_domains(settings: Settings) -> set[str]:
    return {value.strip().lower().lstrip("@") for value in settings.GMAIL_ALLOWED_SENDER_DOMAINS.split(",") if value.strip()}


def _allowed_label_ids(settings: Settings) -> list[str]:
    values = [value.strip() for value in settings.GMAIL_ALLOWED_LABEL_IDS.split(",") if value.strip()]
    return values or ["INBOX"]


def _client_id(settings: Settings) -> str:
    return settings.GMAIL_CLIENT_ID or settings.GOOGLE_CALENDAR_CLIENT_ID


def _client_secret(settings: Settings) -> str:
    return settings.GMAIL_CLIENT_SECRET or settings.GOOGLE_CALENDAR_CLIENT_SECRET


def _fernet(settings: Settings) -> Fernet:
    configured_key = settings.GMAIL_TOKEN_ENCRYPTION_KEY.strip() or settings.GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY.strip()
    if configured_key:
        return Fernet(configured_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()))


def _utc_naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _isoformat(value: Any) -> str | None:
    return value.replace(tzinfo=value.tzinfo or UTC).isoformat() if isinstance(value, datetime) else None
