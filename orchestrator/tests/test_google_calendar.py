"""Tests for the Google Calendar connection boundary."""

import pytest
from fastapi import HTTPException

from orchestrator.api import calendar
from orchestrator.api import autonomy
from orchestrator.api.auth import UserProfile
from orchestrator.config import Settings
from orchestrator.core.google_calendar import (
    _derive_context_window,
    _event_time,
    decrypt_calendar_secret,
    encrypt_calendar_secret,
)
from orchestrator.core.permissions import Role


def configured_settings() -> Settings:
    """Return safe fake OAuth settings for unit tests."""
    return Settings(
        GOOGLE_CALENDAR_ENABLED=True,
        GOOGLE_CALENDAR_CLIENT_ID="client-id",
        GOOGLE_CALENDAR_CLIENT_SECRET="client-secret",
        GOOGLE_CALENDAR_REDIRECT_URI="http://testserver/integrations/google-calendar/callback",
        SECRET_KEY="test-secret",
    )


def test_calendar_page_is_served(client) -> None:
    """The integration setup page is reachable from the UI."""
    response = client.get("/integrations/google-calendar")

    assert response.status_code == 200
    assert "Google Calendar context" in response.text
    assert "Calendar text is not treated as an instruction" in response.text
    assert "window.top.location.assign" in response.text
    assert ".setup[hidden] { display: none !important; }" in response.text
    assert "Preview autonomous review" in response.text
    assert "/integrations/google-calendar/context/review" in response.text


def test_oauth_state_binds_callback_to_user() -> None:
    """OAuth state is signed, typed, and retains no Google credentials."""
    config = configured_settings()
    actor = UserProfile(id=4, email="owner@example.com", role=Role.HOMEOWNER.value, household_id=9, is_active=True)

    state = calendar._create_state(actor, config)
    payload = calendar._decode_state(state, config)

    assert payload["sub"] == "4"
    assert payload["household_id"] == 9
    assert payload["type"] == "google_calendar_oauth"


def test_invalid_oauth_state_is_rejected() -> None:
    """A callback cannot be accepted without valid signed state."""
    with pytest.raises(HTTPException) as error:
        calendar._decode_state("not-a-token", configured_settings())

    assert error.value.status_code == 400


def test_calendar_token_is_encrypted_at_rest() -> None:
    """OAuth secrets are reversible only with the deployment encryption key."""
    config = configured_settings()
    encrypted = encrypt_calendar_secret("refresh-secret", config)

    assert encrypted != "refresh-secret"
    assert decrypt_calendar_secret(encrypted, config) == "refresh-secret"


def test_connect_requires_authenticated_household_member() -> None:
    """Demo/anonymous visitors cannot link an arbitrary calendar."""
    with pytest.raises(HTTPException) as error:
        calendar._require_actor(None)

    assert error.value.status_code == 401


def test_local_setup_requires_its_configured_passphrase() -> None:
    """A demo browser cannot start OAuth without its one-time local proof."""
    config = configured_settings()
    config.COMMAND_CENTER_AUTH_REQUIRED = False
    config.GOOGLE_CALENDAR_SETUP_PASSPHRASE = "local-proof"

    calendar._validate_local_setup_passphrase("local-proof", config)

    with pytest.raises(HTTPException) as error:
        calendar._validate_local_setup_passphrase("wrong-proof", config)

    assert error.value.status_code == 401


def test_local_setup_is_unavailable_when_normal_auth_is_enabled() -> None:
    """The passphrase route is restricted to the explicitly unauthenticated demo."""
    config = configured_settings()
    config.COMMAND_CENTER_AUTH_REQUIRED = True
    config.GOOGLE_CALENDAR_SETUP_PASSPHRASE = "local-proof"

    assert calendar._local_setup_available(config, None) is False


def test_calendar_event_is_reduced_to_non_identifying_away_context() -> None:
    """Event titles are classified transiently and never appear in retained context."""
    context = _derive_context_window(
        {
            "id": "private-google-event-id",
            "summary": "Family vacation to the coast",
            "start": {"dateTime": "2026-10-12T09:00:00-05:00"},
            "end": {"dateTime": "2026-10-19T18:00:00-05:00"},
        }
    )

    assert context is not None
    assert context["mode"] == "away"
    assert "vacation" not in str(context).lower()
    assert context["guidance"]
    assert context["starts_at"].isoformat() == "2026-10-12T14:00:00"


def test_google_event_time_keeps_its_local_offset_for_display() -> None:
    """Raw UI events preserve Google's local timezone while storage is UTC."""
    event_time = _event_time({"dateTime": "2026-10-12T09:00:00-05:00"})

    assert event_time is not None
    assert event_time.isoformat() == "2026-10-12T09:00:00-05:00"


@pytest.mark.asyncio
async def test_calendar_preview_queues_only_advisory_energy_and_security_reviews(monkeypatch) -> None:
    """Calendar previews route through specialists without a device action."""
    submitted = []

    async def fake_submit(task) -> str:
        submitted.append(task)
        return f"task-{len(submitted)}"

    monkeypatch.setattr(autonomy._energy_orchestrator, "submit", fake_submit)

    reviews = await autonomy.submit_calendar_preview_reviews({"mode": "away"})

    assert reviews == [{"kind": "energy", "task_id": "task-1"}, {"kind": "security", "task_id": "task-2"}]
    assert [task.metadata["routed_agent"] for task in submitted] == ["energy", "security"]
    assert all(task.metadata["source"] == "calendar_context" for task in submitted)
    assert all(task.payload["recommendation_only"] is True for task in submitted)
