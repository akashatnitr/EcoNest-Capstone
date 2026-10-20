"""Tests for contextual trip and hosting household plans."""

from unittest.mock import AsyncMock

from orchestrator.core import household_plan


async def test_household_plan_uses_a_local_away_plan_when_gemma_is_unavailable(
    monkeypatch,
) -> None:
    """A connected travel window remains useful when local inference is down."""
    client = type("FailingClient", (), {"generate_structured": AsyncMock(side_effect=RuntimeError()), "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={
            "notices": [
                {
                    "category": "travel",
                    "starts_at": "2026-10-09T09:00:00+00:00",
                    "ends_at": "2026-10-12T20:00:00+00:00",
                }
            ]
        },
        weather={"current": {"temperature": 84, "temperature_unit": "°F"}},
        snapshot={"occupancy_status": "home"},
    )

    assert result["available"] is True
    assert result["source"] == "EcoNest plan"
    assert result["mode"] == "away"
    assert result["plan"]["title"] == "Away-from-home plan"
    assert any(item["label"] == "Garage door" for item in result["plan"]["settings"])
    assert result["plan"]["settings"][0]["label"] == "Away cooling"
    assert "82°F" in result["plan"]["settings"][0]["recommendation"]
    assert "air-conditioning energy" in result["plan"]["settings"][0]["recommendation"]
    assert "energy-saving cooling target" in result["plan"]["summary"]


async def test_away_plan_proposes_current_state_actions_without_executing_them(
    monkeypatch,
) -> None:
    """Away plans turn live state into actions that still await user confirmation."""
    client = type("FailingClient", (), {"generate_structured": AsyncMock(side_effect=RuntimeError()), "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [{"mode": "away", "starts_at": "2026-10-09T09:00:00+00:00"}], "upcoming_contexts": []},
        gmail={"notices": []},
        weather={"current": {"temperature": 84, "temperature_unit": "°F"}},
        snapshot={
            "lights_on": [{"entity_id": "light.kitchen", "name": "Kitchen"}],
            "covers_open": [{"entity_id": "cover.garage", "name": "Garage door"}],
            "temperature_readings": [{"entity_id": "climate.living_room", "name": "Living Room"}],
        },
    )

    assert result["plan"]["actions"] == [
        {"entity_id": "light.kitchen", "entity_name": "Kitchen", "action": "turn_off", "temperature": None},
        {"entity_id": "cover.garage", "entity_name": "Garage door", "action": "close", "temperature": None},
        {"entity_id": "climate.living_room", "entity_name": "Living Room", "action": "set_temperature", "temperature": 82.0},
    ]


async def test_away_plan_does_not_turn_off_a_light_with_inferred_room_activity(
    monkeypatch,
) -> None:
    """An active motion-enabled dimmer is preserved for human review during an away plan."""
    client = type("FailingClient", (), {"generate_structured": AsyncMock(side_effect=RuntimeError()), "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [{"mode": "away", "starts_at": "2026-10-09T09:00:00+00:00"}], "upcoming_contexts": []},
        gmail={"notices": []},
        weather={"current": {"temperature": 84, "temperature_unit": "°F"}},
        snapshot={
            "lights_on": [{"entity_id": "light.media_room", "name": "Media Room"}],
            "inferred_room_activity": [{"entity_id": "light.media_room", "name": "Media Room"}],
        },
    )

    assert result["plan"]["actions"] == []
    assert any("dimmer" in item.lower() for item in result["plan"]["watch_for"])


async def test_household_plan_replaces_an_unbounded_model_temperature(
    monkeypatch,
) -> None:
    """The resident should never see an unexplained model-selected away setpoint."""
    generated = household_plan.HouseholdPlan(
        title="Away Period Plan",
        summary="Prepare the home for the trip.",
        settings=[
            household_plan.HouseholdPlanItem(
                label="Temperature", recommendation="Maintain 68°F.")
        ],
        expected_patterns=["Low activity"],
        watch_for=["Unexpected motion"],
    )
    client = type(
        "GeneratedPlanClient",
        (),
        {"generate_structured": AsyncMock(return_value=generated), "close": AsyncMock()},
    )
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={"notices": [{"category": "travel", "starts_at": "2026-10-09T09:00:00+00:00"}]},
        weather={"current": {"temperature": 84, "temperature_unit": "°F"}},
        snapshot={},
    )

    temperature = result["plan"]["settings"][0]
    assert result["source"] == "Gemma"
    assert temperature["label"] == "Away cooling"
    assert "82°F" in temperature["recommendation"]
    assert "68°F" not in temperature["recommendation"]
    assert "occupied comfort setting" in result["plan"]["summary"]


async def test_household_plan_includes_saved_category_preferences_in_prompt(
    monkeypatch,
) -> None:
    """A resident's edited away plan guides later away-plan reasoning."""
    generated = household_plan.HouseholdPlan(
        title="Away plan",
        summary="Use the resident's preferred plan format.",
        settings=[household_plan.HouseholdPlanItem(label="Lights", recommendation="Turn off non-essential lights.")],
        expected_patterns=["Low activity"],
        watch_for=["Unexpected motion"],
    )
    generate = AsyncMock(return_value=generated)
    client = type("GeneratedPlanClient", (), {"generate_structured": generate, "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={"notices": [{"category": "travel", "starts_at": "2026-10-09T09:00:00+00:00"}]},
        weather={"current": {"temperature": 84, "temperature_unit": "°F"}},
        snapshot={},
        preferences={"away": {"title": "My away plan", "summary": "Prefer quiet nights."}},
    )

    prompt = generate.await_args.args[0][1].content
    assert "Resident feedback from a previous plan" in prompt
    assert "My away plan" in prompt


def test_away_temperature_guidance_splits_mild_weather_between_modes() -> None:
    """Mild weather should still yield a clear away heating or cooling target."""
    heating = household_plan._temperature_guidance(
        {"current": {"temperature": 62, "temperature_unit": "°F"}}, "away"
    )
    cooling = household_plan._temperature_guidance(
        {"current": {"temperature": 63, "temperature_unit": "°F"}}, "away"
    )

    assert heating.label == "Away heating"
    assert "62°F" in heating.recommendation
    assert cooling.label == "Away cooling"
    assert "82°F" in cooling.recommendation


async def test_household_plan_uses_current_mode_without_a_relevant_window(
    monkeypatch,
) -> None:
    """No trip or hosting context must never turn into an invented away plan."""
    client = type("FailingClient", (), {"generate_structured": AsyncMock(side_effect=RuntimeError()), "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={"notices": []},
        weather={},
        snapshot={},
    )

    assert result["available"] is True
    assert result["mode"] == "current"
    assert result["plan"]["title"] == "Current at-home plan"


async def test_household_plan_uses_current_mode_when_someone_is_home(
    monkeypatch,
) -> None:
    """Normal occupancy creates a current at-home plan when no event is active."""
    client = type("FailingClient", (), {"generate_structured": AsyncMock(side_effect=RuntimeError()), "close": AsyncMock()})
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={"notices": []},
        weather={},
        snapshot={"occupancy_status": "home"},
    )

    assert result["available"] is True
    assert result["mode"] == "current"
    assert result["window"]["source"] == "Home Assistant"
    assert result["plan"]["title"] == "Current at-home plan"
    assert result["plan"]["actions"] == []


async def test_current_plan_rejects_model_generated_away_wording(monkeypatch) -> None:
    """A model cannot override the verified current-home context with an away plan."""
    generated = household_plan.HouseholdPlan(
        title="Away Context Plan",
        summary="Keep the home prepared while you are away.",
        settings=[household_plan.HouseholdPlanItem(label="Lights", recommendation="Turn off lights.")],
        expected_patterns=["Low activity"],
        watch_for=["Unexpected motion"],
    )
    client = type(
        "GeneratedPlanClient",
        (),
        {"generate_structured": AsyncMock(return_value=generated), "close": AsyncMock()},
    )
    monkeypatch.setattr(household_plan, "LLMClient", lambda: client())

    result = await household_plan.create_household_plan(
        calendar={"active_contexts": [], "upcoming_contexts": []},
        gmail={"notices": []},
        weather={},
        snapshot={},
    )

    assert result["source"] == "EcoNest plan"
    assert result["mode"] == "current"
    assert result["plan"]["title"] == "Current at-home plan"


def test_household_plan_rejects_claims_that_devices_are_already_changed() -> None:
    """A plan card must describe suggested state, not invent device outcomes."""
    plan = household_plan.HouseholdPlan(
        title="Away plan",
        summary="The house will be set for the trip.",
        settings=[
            household_plan.HouseholdPlanItem(
                label="Garage door",
                recommendation="Garage door is closed and secure.",
            )
        ],
        expected_patterns=["Low activity"],
        watch_for=["Unexpected motion"],
    )

    assert household_plan._is_recommendation_only(plan) is False
