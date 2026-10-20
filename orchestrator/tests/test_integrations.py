"""Tests for the unified household-context integration workspace."""

from unittest.mock import AsyncMock

from orchestrator.api import integrations


async def test_integrations_page_explains_the_context_wrapper(client) -> None:
    """The consolidated workspace shows the wrapper's role to residents."""
    response = client.get("/integrations")

    assert response.status_code == 200
    assert "How the context wrapper works" in response.text
    assert "Calendar · Gmail · Weather · Home Assistant" in response.text
    assert "Gemma → policy → MCP / Home Assistant" in response.text
    assert "Household plan" in response.text
    assert "/integrations/household-plan" in response.text
    assert "econest_household_context_card_order" in response.text
    assert 'data-move="up"' in response.text


async def test_weather_summary_returns_live_and_forecast_facts(monkeypatch) -> None:
    """Weather summary exposes Home Assistant facts without a device operation."""
    monkeypatch.setattr(
        integrations,
        "fetch_weather_snapshot",
        AsyncMock(
            return_value=(
                "weather.home",
                {
                    "condition": "sunny",
                    "temperature": 73.0,
                    "temperature_unit": "°F",
                    "humidity": 42.0,
                    "wind_speed": 8.0,
                    "wind_speed_unit": "mph",
                },
                [
                    {
                        "datetime": "2026-10-08T20:00:00+00:00",
                        "condition": "partlycloudy",
                        "temperature": 69.0,
                    }
                ],
            )
        ),
    )

    result = await integrations.weather_summary()

    assert result["available"] is True
    assert result["entity_id"] == "weather.home"
    assert result["current"]["temperature"] == 73.0
    assert result["next_forecast"]["condition"] == "partlycloudy"


async def test_weather_summary_handles_an_unavailable_weather_entity(monkeypatch) -> None:
    """No weather entity should produce a clear read-only availability result."""
    monkeypatch.setattr(
        integrations,
        "fetch_weather_snapshot",
        AsyncMock(return_value=("", {}, [])),
    )

    result = await integrations.weather_summary()

    assert result == {
        "available": False,
        "warning": "Home Assistant weather is unavailable",
    }


async def test_household_plan_reads_shared_database_context_in_sequence(monkeypatch) -> None:
    """The plan route must not use one async database session concurrently."""
    events: list[str] = []

    async def calendar_context(session, owner_id):
        events.append("calendar")
        return {"upcoming_contexts": []}

    async def gmail_context(session, owner_id):
        events.append("gmail")
        return {"notices": []}

    monkeypatch.setattr(integrations.settings, "COMMAND_CENTER_AUTH_REQUIRED", False)
    monkeypatch.setattr(integrations, "current_calendar_context", calendar_context)
    monkeypatch.setattr(integrations, "current_gmail_context", gmail_context)
    monkeypatch.setattr(
        integrations,
        "fetch_weather_snapshot",
        AsyncMock(return_value=("weather.home", {}, [])),
    )
    monkeypatch.setattr(
        integrations,
        "fetch_home_snapshot",
        AsyncMock(return_value={"available": True}),
    )
    monkeypatch.setattr(
        integrations,
        "create_household_plan",
        AsyncMock(return_value={"available": False, "message": "No plan"}),
    )
    monkeypatch.setattr(
        integrations,
        "load_household_plan_preferences",
        AsyncMock(return_value={}),
    )

    result = await integrations.household_plan(None, object())

    assert result == {"available": False, "message": "No plan"}
    assert events == ["calendar", "gmail"]
