"""Tests for the model-assisted Command Center flow."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from orchestrator.api import command
from orchestrator.agents.base import Result
from orchestrator.mcp.models import ToolExecutionResult


@pytest.fixture(autouse=True)
def allow_demo_command_center(monkeypatch):
    """Keep Command Center tests independent from the developer's local .env."""
    monkeypatch.setattr(command.settings, "COMMAND_CENTER_AUTH_REQUIRED", False)


def _inventory() -> dict:
    return {
        "type": "devices",
        "count": 1,
        "devices": [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "state": "on",
                "actions": ["turn_on", "turn_off", "set_brightness"],
            }
        ],
    }


def test_command_page_uses_prompt_and_confirmation(client):
    """The UI must not retain manual entity/action controls."""
    response = client.get("/command")

    assert response.status_code == 200
    assert "Understand command" in response.text
    assert "Yes, send command" in response.text
    assert "const AGENT_WAIT_SECONDS = 150" in response.text
    assert "The local model reads your request against the live Home Assistant device inventory." in response.text
    assert "energy, security, and watering reviews run immediately" not in response.text
    assert 'data.status === "advisory_started"' in response.text
    assert '"/command/task"' in response.text
    assert "Energy recommendation" in response.text
    assert "Security assessment" in response.text
    assert "Recent important events" in response.text
    assert "View ${events.length} matching records" in response.text
    assert "Current room condition" in response.text
    assert "Technical details" in response.text
    assert "function technicalDetails(data, interpretationMessage)" in response.text
    assert "interpretation.classList.add(\"hide\")" in response.text
    assert "Prompt history" in response.text
    assert "econest_prompt_history" in response.text
    assert "econest_prompt_browser_timing" in response.text
    assert "recordBrowserTiming(timing" in response.text
    assert "data-history-index" in response.text
    assert 'id="feedbackForm"' in response.text
    assert 'id="feedbackRating"' in response.text
    assert 'id="feedbackComment"' in response.text
    assert 'id="feedbackCorrection"' in response.text
    assert 'id="entityId"' not in response.text
    assert 'id="action"' not in response.text


def test_feedback_requires_a_completed_task_owned_by_the_resident(
    client, override_mysql_session, monkeypatch
):
    """A guessed task ID cannot be rated or associated with another resident."""
    monkeypatch.setattr(command._command_orchestrator, "get_task_context", lambda _: ("Question", "42"))
    monkeypatch.setattr(
        command._command_orchestrator,
        "get_result",
        AsyncMock(return_value=Result(success=True, agent="energy", message="Done")),
    )
    response = client.post(
        "/command/feedback",
        json={"task_id": "someone-elses-task", "rating": 5, "comment": "Good"},
    )

    assert response.status_code == 404


def test_feedback_saves_server_result_and_validates_rating(
    client, override_mysql_session, monkeypatch
):
    """The saved prompt and answer come from the completed server task."""
    monkeypatch.setattr(command._command_orchestrator, "get_task_context", lambda _: ("When was the last dryer cycle?", "0"))
    monkeypatch.setattr(
        command._command_orchestrator,
        "get_result",
        AsyncMock(return_value=Result(success=True, agent="event_history", data={"answer": "Yesterday at 4 PM."}, message="Retrieved")),
    )
    save = AsyncMock(return_value={"task_id": "dryer-task", "rating": 3, "comment": "Time was wrong", "saved": True})
    monkeypatch.setattr(command, "save_command_feedback", save)

    invalid = client.post("/command/feedback", json={"task_id": "dryer-task", "rating": 6})
    valid = client.post(
        "/command/feedback",
        json={"task_id": "dryer-task", "rating": 3, "comment": " Time was wrong "},
    )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert save.await_args.kwargs["prompt"] == "When was the last dryer cycle?"
    assert save.await_args.kwargs["response_text"] == "Yesterday at 4 PM."
    assert save.await_args.kwargs["comment"] == "Time was wrong"


def test_feedback_correction_is_pending_not_automatically_approved(
    client, override_mysql_session, monkeypatch
):
    monkeypatch.setattr(command._command_orchestrator, "get_task_context", lambda _: ("Question", "0"))
    monkeypatch.setattr(
        command._command_orchestrator, "get_result",
        AsyncMock(return_value=Result(success=True, agent="energy", message="Done")),
    )
    save = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr(command, "save_command_feedback", save)
    response = client.post(
        "/command/feedback",
        json={"task_id": "task-1", "rating": 2, "correction": " Check the dryer reading first. "},
    )
    assert response.status_code == 200
    assert save.await_args.kwargs["correction"] == "Check the dryer reading first."


def test_demo_cannot_review_resident_corrections(client, override_mysql_session):
    response = client.get("/command/feedback/pending")
    assert response.status_code == 401


def test_review_permission_requires_superadmin(test_user, admin_user):
    with pytest.raises(HTTPException) as denied:
        command._feedback_reviewer(test_user)
    assert denied.value.status_code == 403
    assert command._feedback_reviewer(admin_user) == admin_user


def test_interpret_command_starts_watering_recommendation_without_confirmation(
    client, override_current_user, monkeypatch
):
    """Watering questions use the advisory path and never prepare valve control."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="watering_recommendation",
                confidence=0.91,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="watering-review-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Should I water the lawn today?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "advisory_started"
    assert response.json()["task_id"] == "watering-review-task"
    assert submit.await_args.kwargs["payload"] == {
        "type": "irrigation",
        "recommendation_only": True,
        "use_llm": True,
    }


def test_should_i_water_question_skips_device_matching(client, override_current_user, monkeypatch):
    """Watering advice cannot be converted into a valve-control confirmation."""
    read_resource = AsyncMock(side_effect=AssertionError("inventory should not be read"))
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", read_resource)
    submit = AsyncMock(return_value="watering-review-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Should I water the garden today?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "advisory_started"
    assert response.json()["request_kind"] == "watering_recommendation"
    assert response.json()["task_id"] == "watering-review-task"
    read_resource.assert_not_awaited()
    assert submit.await_args.kwargs["payload"] == {
        "type": "irrigation",
        "recommendation_only": True,
        "use_llm": True,
    }


def test_interpret_command_starts_irrigation_question_without_confirmation(
    client, override_current_user, monkeypatch
):
    """Factual irrigation questions start a read-only answer task."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="irrigation_question",
                confidence=0.91,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="irrigation-question-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Was the sprinkler on in the garden today?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answer_started"
    assert submit.await_args.kwargs["payload"] == {"type": "irrigation_question", "recommendation_only": True}


def test_when_irrigation_last_ran_skips_appliance_history_and_device_matching(
    client, override_current_user, monkeypatch
):
    """A factual irrigation question must query irrigation_runs, not appliance events."""
    read_resource = AsyncMock(side_effect=AssertionError("inventory should not be read"))
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", read_resource)
    submit = AsyncMock(return_value="irrigation-question-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "When did the irrigation system last run?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answer_started"
    assert response.json()["request_kind"] == "irrigation_question"
    read_resource.assert_not_awaited()
    assert submit.await_args.kwargs["payload"] == {
        "type": "irrigation_question",
        "recommendation_only": True,
    }


def test_recently_watered_question_starts_irrigation_history(
    client, override_current_user, monkeypatch
):
    """A past-watering question cannot be converted into weather advice."""
    read_resource = AsyncMock(side_effect=AssertionError("inventory should not be read"))
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", read_resource)
    submit = AsyncMock(return_value="irrigation-question-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Has the lawn been watered recently?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answer_started"
    assert response.json()["request_kind"] == "irrigation_question"
    read_resource.assert_not_awaited()
    assert submit.await_args.kwargs["payload"] == {
        "type": "irrigation_question",
        "recommendation_only": True,
    }


def test_interpret_command_starts_event_history_without_device_matching(
    client, override_current_user, monkeypatch
):
    """Recent-event questions are a read-only history task, not a device action."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="event_history", confidence=0.92)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="event-history-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Tell me about the recent important events in my home."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answer_started"
    assert response.json()["request_kind"] == "event_history"
    assert submit.await_args.kwargs["payload"] == {
        "type": "event_history",
        "recommendation_only": True,
    }


def test_interpret_command_routes_last_appliance_cycle_to_event_history(
    client, override_current_user, monkeypatch
):
    """Appliance-cycle questions are history lookups, never device commands."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="historical_analysis",
                confidence=0.92,
                history_analysis={
                    "scope": "appliance_cycles",
                    "operation": "latest",
                    "subject": "dryer",
                },
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="dryer-cycle-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post("/command/interpret", json={"intent": "When was the last dryer cycle?"})

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert submit.await_args.kwargs["payload"] == {
        "type": "event_history",
        "recommendation_only": True,
        "history_analysis": {
            "scope": "appliance_cycles",
            "operation": "latest",
            "subject": "dryer",
            "period_days": None,
        },
    }


def test_gemma_plans_historical_duration_analysis_for_open_ended_question(
    client, override_current_user, monkeypatch
):
    """Gemma chooses a bounded analysis; it never receives direct database access."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="historical_analysis",
                confidence=0.93,
                history_analysis={
                    "scope": "appliance_cycles",
                    "operation": "average_duration",
                    "subject": "dryer",
                    "period_days": 183,
                },
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="history-analysis-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What is the average length of my dryer cycles over the past 6 months?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert submit.await_args.kwargs["payload"] == {
        "type": "event_history",
        "recommendation_only": True,
        "history_analysis": {
            "scope": "appliance_cycles",
            "operation": "average_duration",
            "subject": "dryer",
            "period_days": 183,
        },
    }


def test_history_metric_validator_corrects_gemma_energy_misclassification(
    client, override_current_user, monkeypatch
):
    """A factual cycle metric cannot be sent to an advisory-only energy agent."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="energy_recommendation", confidence=0.8)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="corrected-history-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What is the average length of my dryer cycles over the past 6 months?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert submit.await_args.kwargs["payload"]["history_analysis"] == {
        "scope": "appliance_cycles",
        "operation": "average_duration",
        "subject": "dryer",
        "period_days": 180,
    }


@pytest.mark.parametrize(
    ("intent", "operation", "subject", "days"),
    [
        ("What is the average run time of my dryer in the past 6 months?", "average_duration", "dryer", 180),
        ("What is the avg runtime of my clothes dryer over the past 6 months?", "average_duration", "clothes dryer", 180),
        ("What is the average cycle duration of my washer over the past 2 months?", "average_duration", "washer", 60),
        ("How long do my dryer loads usually take over the last six months?", "average_duration", "dryer", 180),
        ("What was the most recent completed dryer cycle?", "latest", "dryer", None),
        ("How many dryer cycles have been completed in the past 30 days?", "count", "dryer", 30),
        ("Show my latest washer cycle.", "latest", "washer", None),
        ("What is the total time my dryer has run over the past 90 days?", "total_duration", "dryer", 90),
        ("How long did my washer run this month?", "total_duration", "washer", 30),
    ],
)
def test_history_plan_recognizes_appliance_runtime_wording(intent, operation, subject, days):
    """Natural history phrasing must select retained cycles, not live power readings."""
    plan = command._history_plan_hint(intent)

    assert plan is not None
    assert plan.scope == "appliance_cycles"
    assert plan.operation == operation
    assert plan.subject == subject
    assert plan.period_days == days


def test_history_plan_recognizes_a_named_year_window():
    """A retained-history question with no matching appliance remains answerable."""
    plan = command._history_plan_hint(
        "What is the average run time of my toaster over the past year?"
    )

    assert plan is not None
    assert plan.operation == "average_duration"
    assert plan.subject == "toaster"
    assert plan.period_days == 365


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("Turn on the lights.", "specific light"),
        ("Make it warmer.", "room and target temperature"),
        ("Turn on the dryer.", "specific appliance control"),
        ("Turn on every light in the house and unlock the doors.", "one specific device"),
    ],
)
def test_broad_or_multi_device_actions_require_clarification(client, intent, expected):
    """An underspecified or multi-device action must never receive a proposal."""
    response = client.post("/command/interpret", json={"intent": intent})

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert expected in response.json()["message"]


def test_dryer_runtime_question_corrects_gemma_energy_misclassification(
    client, override_current_user, monkeypatch
):
    """The user's exact question must not become an energy recommendation."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="energy_recommendation", confidence=0.8)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="dryer-runtime-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What is the average run time of my dryer in the past 6 months?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert response.json()["status"] == "answer_started"
    assert submit.await_args.kwargs["payload"] == {
        "type": "event_history",
        "recommendation_only": True,
        "history_analysis": {
            "scope": "appliance_cycles",
            "operation": "average_duration",
            "subject": "dryer",
            "period_days": 180,
        },
    }


def test_history_metric_overrides_an_incorrect_gemma_history_operation(
    client, override_current_user, monkeypatch
):
    """A count question cannot become a latest-event plan."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="historical_analysis",
                confidence=0.8,
                history_analysis={
                    "scope": "appliance_cycles",
                    "operation": "latest",
                    "subject": "dryer",
                },
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="dryer-count-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "How many dryer cycles have been completed in the past 30 days?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert submit.await_args.kwargs["payload"]["history_analysis"] == {
        "scope": "appliance_cycles",
        "operation": "count",
        "subject": "dryer",
        "period_days": 30,
    }


@pytest.mark.parametrize(
    ("intent", "operation", "subject", "days"),
    [
        ("How long do my dryer loads usually take over the last six months?", "average_duration", "dryer", 180),
        ("What was the most recent completed dryer cycle?", "latest", "dryer", None),
        ("How many dryer cycles have been completed in the past 30 days?", "count", "dryer", 30),
        ("Show my latest washer cycle.", "latest", "washer", None),
        ("What is the total time my dryer has run over the past 90 days?", "total_duration", "dryer", 90),
        ("How long did my washer run this month?", "total_duration", "washer", 30),
    ],
)
def test_history_questions_override_gemma_device_misclassification(
    client, override_current_user, monkeypatch, intent, operation, subject, days
):
    """Factual appliance questions cannot fall through to a device clarification."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="device_control", confidence=0.2)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="history-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post("/command/interpret", json={"intent": intent})

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert response.json()["status"] == "answer_started"
    assert submit.await_args.kwargs["payload"]["history_analysis"] == {
        "scope": "appliance_cycles",
        "operation": operation,
        "subject": subject,
        "period_days": days,
    }


def test_factual_runtime_question_takes_priority_over_energy_keyword(
    client, override_current_user, monkeypatch
):
    """A mention of energy must not skip the historical-question validator."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="energy_recommendation", confidence=0.8)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="dryer-runtime-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "For energy tracking, what is the average run time of my dryer in the past 6 months?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "historical_analysis"
    assert submit.await_args.kwargs["payload"]["history_analysis"]["subject"] == "dryer"


def test_room_comfort_validator_corrects_gemma_energy_misclassification(
    client, override_current_user, monkeypatch
):
    """Current room-state facts cannot be sent to the advisory energy agent."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(request_kind="energy_recommendation", confidence=0.8)

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="room-state-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What is my comfort temperature in the media room?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "home_state_query"
    assert submit.await_args.kwargs["payload"] == {
        "type": "home_data",
        "recommendation_only": True,
        "room_comfort_query": {"room": "media room", "metric": "target_temperature"},
    }


def test_hvac_mode_question_overrides_gemma_temperature_metric(
    client, override_current_user, monkeypatch
):
    """A mode question cannot be answered with a thermostat target."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="home_state_query",
                confidence=0.8,
                room_comfort_query={
                    "room": "media room",
                    "metric": "target_temperature",
                },
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="room-mode-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What HVAC mode is the media room using?"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "home_state_query"
    assert response.json()["status"] == "answer_started"
    assert submit.await_args.kwargs["payload"]["room_comfort_query"] == {
        "room": "media room",
        "metric": "hvac_mode",
    }


def test_conditional_light_command_checks_irrigation_before_confirmation(
    client, override_current_user, monkeypatch
):
    """A supported condition is retained and checked through MCP before confirmation."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )
    execute = AsyncMock(
        return_value=ToolExecutionResult(
            capability="query_mysql", result=[{"run_count": 0, "last_run_at": None}]
        )
    )
    monkeypatch.setattr(command.MCPToolExecutor, "execute", execute)

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="light.upstairs_media_light_1",
                action="turn_off",
                confidence=0.94,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={"intent": "Turn off the media room light if sprinkler has not been used in the last 2 days"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert "no recorded irrigation runs in the last 2 days" in response.json()["message"]
    assert execute.await_args.kwargs["agent"] == "command_condition_evaluator"


def test_conditional_light_command_accepts_irrigation_was_on_today(
    client, override_current_user, monkeypatch
):
    """A positive same-day irrigation condition is checked before confirmation."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "execute",
        AsyncMock(
            return_value=ToolExecutionResult(
                capability="query_mysql",
                result=[{"run_count": 2, "last_run_at": "2026-09-28T10:00:00"}],
            )
        ),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="light.upstairs_media_light_1",
                action="turn_off",
                confidence=0.94,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={
            "intent": "Turn off the media room light if the sprinkler was turned on in the garden today"
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert "recorded irrigation run today" in response.json()["message"]


def test_discovered_sensor_condition_is_checked_before_confirmation(
    client, override_current_user, monkeypatch
):
    """A condition plan can use a newly discovered scalar HA attribute."""
    condition_catalog = {
        "conditions": [
            {
                "entity_id": "sensor.study_humidity",
                "properties": ["state"],
            }
        ]
    }
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(side_effect=[_inventory(), condition_catalog]),
    )
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "execute",
        AsyncMock(
            return_value=ToolExecutionResult(
                capability="ha_get_state", result={"state": "68", "attributes": {}}
            )
        ),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="light.upstairs_media_light_1",
                action="turn_off",
                conditions=[
                    {
                        "entity_id": "sensor.study_humidity",
                        "property": "state",
                        "operator": "greater_than",
                        "value": 65,
                    }
                ],
                confidence=0.94,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={"intent": "Turn off the media room light if study humidity is above 65"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert "sensor.study_humidity state" in response.json()["message"]


def test_relevant_condition_capabilities_keeps_matching_room_sensor():
    """The planner gets a small relevant slice rather than every HA entity."""
    conditions = [
        {"entity_id": "sensor.garage_temperature", "name": "Garage Temperature", "domain": "sensor"},
        {"entity_id": "climate.media_room", "name": "Media Room Thermostat", "domain": "climate"},
        {"entity_id": "sensor.kitchen_power", "name": "Kitchen Power", "domain": "sensor"},
    ]

    selected = command._relevant_condition_capabilities(
        "Turn on the media room light if the thermostat is at 82 degrees", conditions
    )

    assert selected[0]["entity_id"] == "climate.media_room"


def test_unambiguous_fallback_completes_light_and_thermostat_plan():
    """A small-model omission can be completed only from matching live inventory."""
    completed = command._complete_unambiguous_plan(
        "Turn on the media room light if the thermostat in media room is at 82 degrees Fahrenheit",
        command.CommandInterpretation(confidence=0.0),
        [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "actions": ["turn_on"],
            }
        ],
        [
            {
                "entity_id": "climate.media_room",
                "name": "Media Room Thermostat",
                "domain": "climate",
                "properties": ["temperature", "current_temperature"],
            }
        ],
    )

    assert completed.entity_id == "light.upstairs_media_light_1"
    assert completed.action == "turn_on"
    assert completed.conditions[0].entity_id == "climate.media_room"
    assert completed.conditions[0].value == 82


def test_unambiguous_fallback_replaces_a_hallucinated_entity():
    """A model target outside the retrieved inventory is never retained."""
    completed = command._complete_unambiguous_plan(
        "Turn on the media room light",
        command.CommandInterpretation(
            entity_id="light.media_room",
            action="turn_on",
            confidence=0.5,
        ),
        [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "actions": ["turn_on"],
            }
        ],
        [],
    )

    assert completed.entity_id == "light.upstairs_media_light_1"


def test_unambiguous_fallback_prioritizes_the_user_control_verb():
    """A thermostat mention must not override an explicit light turn-on request."""
    completed = command._complete_unambiguous_plan(
        "Turn on the media room light if the thermostat is at 82 degrees",
        command.CommandInterpretation(
            entity_id="climate.media_room",
            action="set_temperature",
            confidence=0.5,
        ),
        [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "actions": ["turn_on"],
            }
        ],
        [],
    )

    assert completed.action == "turn_on"
    assert completed.entity_id == "light.upstairs_media_light_1"


def test_light_request_with_thermostat_condition_recovers_from_wrong_model_action(
    client, override_current_user, monkeypatch
):
    """The complete command route keeps control target and condition separate."""
    condition_catalog = {
        "conditions": [
            {
                "entity_id": "climate.media_room",
                "name": "Media Room Thermostat",
                "domain": "climate",
                "properties": ["temperature", "current_temperature"],
            }
        ]
    }
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(side_effect=[_inventory(), condition_catalog]),
    )
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "execute",
        AsyncMock(
            return_value=ToolExecutionResult(
                capability="ha_get_state",
                result={"state": "cool", "attributes": {"temperature": 82.0}},
            )
        ),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="climate.media_room",
                action="set_temperature",
                confidence=0.5,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={
            "intent": "Turn on the media room light if the thermostat in media room is at 82 degrees Fahrenheit"
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert response.json()["entity_id"] == "light.upstairs_media_light_1"


def test_condition_numeric_equality_accepts_home_assistant_number_formats():
    """Home Assistant's 82.0 and a model's 82 represent the same setpoint."""
    condition = command.ConditionSpec(
        entity_id="climate.media_room",
        property="temperature",
        operator="equals",
        value=82,
    )

    assert command._condition_matches("82.0", condition)


def test_explicit_temperature_target_overrides_an_incorrect_sensor_plan():
    """The user's target/setpoint wording selects climate.temperature and its value."""
    completed = command._complete_unambiguous_plan(
        "Turn on the media room light if the temperature target in media room is at 80 degrees Fahrenheit",
        command.CommandInterpretation(
            entity_id="light.upstairs_media_light_1",
            action="turn_on",
            conditions=[
                {
                    "entity_id": "sensor.media_room_temperature",
                    "property": "state",
                    "operator": "equals",
                    "value": 82,
                }
            ],
            confidence=0.5,
        ),
        [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "actions": ["turn_on"],
            }
        ],
        [
            {
                "entity_id": "climate.media_room",
                "name": "Media Room Thermostat",
                "domain": "climate",
                "properties": ["temperature"],
            }
        ],
    )

    assert completed.conditions[0].entity_id == "climate.media_room"
    assert completed.conditions[0].property == "temperature"
    assert completed.conditions[0].value == 80


def test_command_access_reports_temporary_demo_mode(client):
    """The browser can decide whether to present the sign-in form."""
    response = client.get("/command/access")

    assert response.status_code == 200
    assert response.json() == {"authentication_required": False}


def test_command_access_requires_auth_when_demo_mode_is_disabled(client, monkeypatch):
    """Turning off demo mode restores the authenticated command boundary."""
    monkeypatch.setattr(command.settings, "COMMAND_CENTER_AUTH_REQUIRED", True)

    response = client.post(
        "/command/interpret", json={"intent": "Turn off the media room light"}
    )

    assert response.status_code == 401


def test_interpret_command_returns_inventory_backed_confirmation(
    client, override_current_user, monkeypatch
):
    """A model result is offered only when it names a listed capable device."""
    resource_read = AsyncMock(return_value=_inventory())
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", resource_read)

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            assert "Turn off the media room light" in messages[0].content
            return output_model(
                entity_id="light.upstairs_media_light_1",
                action="turn_off",
                confidence=0.94,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={"intent": "Turn off the media room light"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmation_required"
    assert data["entity_id"] == "light.upstairs_media_light_1"
    assert data["action"] == "turn_off"
    assert resource_read.await_count == 2
    assert {call.args[0] for call in resource_read.await_args_list} == {
        "home://devices",
        "home://conditions",
    }
    assert resource_read.await_args.kwargs["agent"] == "command_interpreter"


def test_unconditional_brightness_command_discards_model_invented_condition(
    client, override_current_user, monkeypatch
):
    """A normal dim request must not be blocked by an unrequested predicate."""
    condition_catalog = {
        "conditions": [
            {
                "entity_id": "light.upstairs_media_light_1",
                "name": "Media Room Light",
                "domain": "light",
                "properties": ["state"],
            }
        ]
    }
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(side_effect=[_inventory(), condition_catalog]),
    )
    execute = AsyncMock(side_effect=AssertionError("no condition should be evaluated"))
    monkeypatch.setattr(command.MCPToolExecutor, "execute", execute)

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="light.upstairs_media_light_1",
                action="set_brightness",
                brightness=30,
                conditions=[
                    {
                        "entity_id": "light.upstairs_media_light_1",
                        "property": "state",
                        "operator": "equals",
                        "value": "on",
                    }
                ],
                confidence=0.9,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret",
        json={"intent": "Dim the media room lights to 30 percent."},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmation_required"
    assert data["action"] == "set_brightness"
    assert data["brightness"] == 30
    assert data["conditions"] == []
    execute.assert_not_awaited()


def test_interpret_command_replaces_hallucinated_entity_when_one_match_is_clear(
    client, override_current_user, monkeypatch
):
    """A model target outside inventory is replaced only by one clear live match."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                entity_id="light.invented",
                action="turn_off",
                confidence=1.0,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    response = client.post(
        "/command/interpret", json={"intent": "Turn off the media room light"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"
    assert response.json()["entity_id"] == "light.upstairs_media_light_1"


def test_interpret_command_starts_energy_recommendation_without_confirmation(
    client, override_current_user, monkeypatch
):
    """Energy questions start an advisory review without device confirmation."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            assert "energy_recommendation" in messages[0].content
            return output_model(
                request_kind="energy_recommendation",
                confidence=0.88,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    submit = AsyncMock(return_value="energy-review-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)
    response = client.post(
        "/command/interpret",
        json={"intent": "How can I reduce my home's energy use?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "advisory_started"
    assert data["request_kind"] == "energy_recommendation"
    assert data["entity_id"] is None
    assert data["task_id"] == "energy-review-task"
    assert submit.await_args.kwargs["payload"] == {
        "type": "energy",
        "recommendation_only": True,
        "use_llm": True,
    }


def test_clear_energy_recommendation_skips_device_inventory_and_model(
    client, override_current_user, monkeypatch
):
    """Plain advisory language must not fall through to device matching."""
    read_resource = AsyncMock(side_effect=AssertionError("inventory should not be read"))
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", read_resource)
    submit = AsyncMock(return_value="energy-review-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "What energy recommendation do you have for my home right now?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "advisory_started"
    assert response.json()["request_kind"] == "energy_recommendation"
    read_resource.assert_not_awaited()
    assert submit.await_args.kwargs["payload"] == {
        "type": "energy",
        "recommendation_only": True,
        "use_llm": True,
    }


def test_unusual_high_power_question_starts_energy_review(
    client, override_current_user, monkeypatch
):
    """A high-power question must not be treated as a device action."""
    read_resource = AsyncMock(side_effect=AssertionError("inventory should not be read"))
    monkeypatch.setattr(command.MCPToolExecutor, "read_resource", read_resource)
    submit = AsyncMock(return_value="energy-review-task")
    monkeypatch.setattr(command._command_orchestrator, "submit_http_api", submit)

    response = client.post(
        "/command/interpret",
        json={"intent": "Is anything using unusually high power right now?"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "advisory_started"
    assert response.json()["request_kind"] == "energy_recommendation"
    read_resource.assert_not_awaited()
    assert submit.await_args.kwargs["payload"] == {
        "type": "energy",
        "recommendation_only": True,
        "use_llm": True,
    }


def test_interpret_command_starts_security_recommendation_without_confirmation(
    client, override_current_user, monkeypatch
):
    """Security questions start an advisory review without device confirmation."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value=_inventory()),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="security_recommendation",
                confidence=0.91,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        command._command_orchestrator,
        "submit_http_api",
        AsyncMock(return_value="security-review-task"),
    )
    response = client.post(
        "/command/interpret",
        json={"intent": "Give me a home security recommendation"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "advisory_started"
    assert data["request_kind"] == "security_recommendation"
    assert data["task_id"] == "security-review-task"


def test_interpret_recommendation_does_not_require_device_inventory(
    client, override_current_user, monkeypatch
):
    """Advisory requests still work when Home Assistant lists no devices."""
    monkeypatch.setattr(
        command.MCPToolExecutor,
        "read_resource",
        AsyncMock(return_value={"type": "devices", "count": 0, "devices": []}),
    )

    class FakeLLMClient:
        async def generate_structured(self, messages, output_model, temperature=0.0):
            return output_model(
                request_kind="energy_recommendation",
                confidence=0.8,
            )

    monkeypatch.setattr(command, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        command._command_orchestrator,
        "submit_http_api",
        AsyncMock(return_value="energy-review-task"),
    )
    response = client.post(
        "/command/interpret",
        json={"intent": "Recommend ways to save energy"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "advisory_started"


def test_relevant_devices_ranks_media_room_light_above_other_matches():
    """Natural plural wording should retrieve the named room light first."""
    devices = [
        {
            "entity_id": "climate.media_room",
            "name": "Media Room",
            "domain": "climate",
        },
        {
            "entity_id": "light.upstairs_media_light_1",
            "name": "Media Room Lights Light 1",
            "domain": "light",
        },
        {
            "entity_id": "light.living_room",
            "name": "Living Room Light",
            "domain": "light",
        },
    ]

    candidates = command._relevant_devices("Turn on the media room lights", devices)

    assert candidates[0]["entity_id"] == "light.upstairs_media_light_1"
