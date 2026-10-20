"""Tests for Gmail's metadata-only connector boundary."""

import base64
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException
import pytest

from orchestrator.api import gmail
from orchestrator.api.auth import UserProfile
from orchestrator.config import Settings
from orchestrator.core.google_gmail import (
    _derive_notice,
    current_gmail_context,
    decrypt_gmail_secret,
    encrypt_gmail_secret,
)
from orchestrator.core.permissions import Role


def configured_settings() -> Settings:
    """Return safe OAuth settings for Gmail unit tests."""
    return Settings(
        GMAIL_ENABLED=True,
        GMAIL_CLIENT_ID="client-id",
        GMAIL_CLIENT_SECRET="client-secret",
        GMAIL_REDIRECT_URI="http://testserver/integrations/gmail/callback",
        GMAIL_ALLOWED_SENDER_DOMAINS="travel.example",
        SECRET_KEY="test-secret",
    )


def test_gmail_page_is_served(client) -> None:
    """The metadata-only connector has a visible setup page."""
    response = client.get("/integrations/gmail")

    assert response.status_code == 200
    assert "Gmail household notices" in response.text
    assert "explicitly enabled test mode" in response.text
    assert "never retains raw email text or attachments" in response.text


async def test_current_gmail_context_excludes_completed_event_windows() -> None:
    """Retained notices whose derived window ended cannot drive a household plan."""
    result = MagicMock()
    result.mappings.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result

    context = await current_gmail_context(session, user_id=1)

    assert context["notices"] == []
    statement = str(session.execute.await_args.args[0])
    assert "ends_at IS NULL OR ends_at >= :now" in statement


def test_household_pages_use_the_combined_upcoming_context_view(client) -> None:
    """User-facing pages label Calendar and Gmail context as distinct sources."""
    command = client.get("/command")
    autonomy = client.get("/autonomy")

    assert "/integrations/google-calendar/upcoming-context" in command.text
    assert "Upcoming household context" in command.text
    assert "/integrations/google-calendar/upcoming-context" in autonomy.text
    assert "Upcoming household context" in autonomy.text


def test_gmail_oauth_state_is_typed_and_bound_to_owner() -> None:
    """OAuth callback state cannot be reused as Calendar state."""
    actor = UserProfile(id=4, email="owner@example.com", role=Role.HOMEOWNER.value, household_id=9, is_active=True)
    state = gmail._create_state(actor, configured_settings())

    assert gmail._decode_state(state, configured_settings())["sub"] == "4"
    with pytest.raises(HTTPException):
        gmail._decode_state("not-a-token", configured_settings())


def test_gmail_tokens_are_encrypted_at_rest() -> None:
    """Refresh tokens are never stored as plaintext."""
    config = configured_settings()
    encrypted = encrypt_gmail_secret("refresh-secret", config)

    assert encrypted != "refresh-secret"
    assert decrypt_gmail_secret(encrypted, config) == "refresh-secret"


def test_gmail_reduces_allowlisted_metadata_without_retaining_text() -> None:
    """Only a category and guidance survive classification."""
    config = configured_settings()
    notice = _derive_notice(
        {
            "id": "private-message-id",
            "internalDate": "1791475200000",
            "payload": {"headers": [
                {"name": "From", "value": "Reservations <notice@travel.example>"},
                {"name": "Subject", "value": "Your vacation itinerary"},
            ]},
        },
        {"travel.example"},
        config,
    )

    assert notice is not None
    assert notice["category"] == "travel"
    assert "vacation" not in str(notice).lower()
    assert "travel.example" not in str(notice).lower()


def test_gmail_rejects_non_allowlisted_sender() -> None:
    """A metadata message outside the allowlist cannot become context."""
    notice = _derive_notice(
        {
            "id": "private-message-id",
            "payload": {"headers": [
                {"name": "From", "value": "notice@other.example"},
                {"name": "Subject", "value": "Trip notification"},
            ]},
        },
        {"travel.example"},
        configured_settings(),
    )

    assert notice is None


def test_test_body_mode_derives_a_trip_window_without_retaining_email_text() -> None:
    """An explicitly enabled test body can produce dates but no raw content."""
    config = configured_settings()
    config.GMAIL_READ_BODY_FOR_CONTEXT = True
    body = base64.urlsafe_b64encode(
        b"Trip to Dallas from October 12, 2026 to October 19, 2026."
    ).decode().rstrip("=")
    notice = _derive_notice(
        {
            "id": "private-message-id",
            "internalDate": "1791475200000",
            "payload": {"mimeType": "text/plain", "body": {"data": body}, "headers": [
                {"name": "From", "value": "notice@travel.example"},
                {"name": "Subject", "value": "Travel details"},
            ]},
        },
        {"travel.example"},
        config,
    )

    assert notice is not None
    assert notice["category"] == "travel"
    assert notice["starts_at"].isoformat() == "2026-10-12T00:00:00"
    assert notice["ends_at"].isoformat() == "2026-10-20T00:00:00"
    assert "dallas" not in str(notice).lower()


def test_test_body_mode_supports_abbreviated_itinerary_date_ranges() -> None:
    """Common compact itinerary text like ``Oct 9-11`` derives a date window."""
    config = configured_settings()
    config.GMAIL_READ_BODY_FOR_CONTEXT = True
    body = base64.urlsafe_b64encode(b"Trip to Dallas: Oct 9-11").decode().rstrip("=")
    notice = _derive_notice(
        {
            "id": "private-message-id",
            "internalDate": "1791475200000",
            "payload": {"mimeType": "text/plain", "body": {"data": body}, "headers": [
                {"name": "From", "value": "notice@travel.example"},
                {"name": "Subject", "value": "Travel details"},
            ]},
        },
        {"travel.example"},
        config,
    )

    assert notice is not None
    assert notice["starts_at"].isoformat() == "2026-10-09T00:00:00"
    assert notice["ends_at"].isoformat() == "2026-10-12T00:00:00"
