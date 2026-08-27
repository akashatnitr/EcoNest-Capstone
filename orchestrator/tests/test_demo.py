"""Tests for the browser demo flow."""

from unittest.mock import AsyncMock

from orchestrator.agents.base import Result
from orchestrator.api import demo
from orchestrator.mcp.models import ToolExecutionResult

import pytest

def test_demo_page_serves_frontend(client):
    response = client.get("/demo")

    assert response.status_code == 200
    assert "EcoNest Demo Console" in response.text
    assert "Demo1" in response.text


def test_demo1_rejects_invalid_code(client):
    response = client.post("/demo/demo1", json={"code": "wrong"})

    assert response.status_code == 403


def test_demo2_rejects_invalid_code(client):
    response = client.post("/demo/demo2", json={"code": "wrong"})

    assert response.status_code == 403


def test_demo3_rejects_invalid_code(client):
    response = client.post("/demo/demo3", json={"code": "wrong"})

    assert response.status_code == 403


def test_demo4_rejects_invalid_code(client):
    response = client.post("/demo/demo4", json={"code": "wrong"})

    assert response.status_code == 403


def test_demo1_streams_readable_progress(client, monkeypatch):
    class FakeOrchestrator:
        async def submit(self, task):
            if task.payload.get("type") == "energy":
                assert task.metadata["event_type"] == "energy_anomaly"
                return "task-energy-1"
            if task.payload["action"] == "turn_on":
                assert task.metadata["source"] == "ha_event_replay"
                return "task-device-1"
            assert task.payload["action"] == "turn_off"
            assert task.metadata["source"] == "llm_policy_recommendation"
            return "task-policy-1"

        async def get_result(self, task_id):
            if task_id == "task-energy-1":
                return Result(
                    success=True,
                    data={
                        "recommendation": "Shift media room load to off-peak hours",
                    },
                    message="Energy review complete",
                    agent="energy",
                    task_id=task_id,
                    confidence=1.0,
                )
            if task_id == "task-policy-1":
                return Result(
                    success=True,
                    data={
                        "action": "turn_off",
                        "verified": True,
                        "execution_source": "home_assistant",
                    },
                    message="Device action completed",
                    agent="device",
                    task_id=task_id,
                    confidence=0.95,
                    metadata={
                        "verified": True,
                        "execution_source": "home_assistant",
                    },
                )
            assert task_id == "task-device-1"
            return Result(
                success=True,
                data={
                    "action": "turn_on",
                    "verified": True,
                    "execution_source": "home_assistant",
                },
                message="Device action completed",
                agent="device",
                task_id=task_id,
                confidence=0.95,
                metadata={
                    "verified": True,
                    "execution_source": "home_assistant",
                },
            )

    class FakeLLMClient:
        async def generate(self, *args, **kwargs):
            return (
                "The media room is vacant and the light is still on. "
                "EcoNest should turn it off to avoid wasting energy."
            )

        async def close(self):
            return None

    ha_state = ToolExecutionResult(
        capability="ha_get_state",
        result={
            "entity_id": demo.DEMO_MEDIA_LIGHT_ENTITY,
            "state": "on",
        },
    )

    monkeypatch.setattr(demo, "healthcheck_mysql", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "healthcheck_arcadedb", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "ha_get_state_handler", AsyncMock(return_value=ha_state))
    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(demo, "_demo_orchestrator", FakeOrchestrator())

    response = client.post("/demo/demo1", json={"code": "Demo1"})

    assert response.status_code == 200
    assert "Demo1 started" in response.text
    assert "Infrastructure check" in response.text
    assert "Energy agent result" in response.text
    assert "Device agent result" in response.text
    assert "LLM policy reasoning" in response.text
    assert "Policy action result" in response.text
    assert "Demo1 complete" in response.text
    assert '"agent": "energy"' in response.text
    assert '"agent": "device"' in response.text
    assert '"agent": "llm"' in response.text
    assert '"recommended_action": "turn_off"' in response.text
    assert '"verified": true' in response.text


def test_demo2_streams_thermostat_reasoning_and_action(client, monkeypatch):
    class FakeOrchestrator:
        async def submit(self, task):
            assert task.payload["action"] == "set_temperature"
            assert task.payload["domain"] == "climate"
            assert task.payload["temperature"] == 78.0
            assert task.metadata["source"] == "llm_climate_recommendation"
            return "task-climate-1"

        async def get_result(self, task_id):
            assert task_id == "task-climate-1"
            return Result(
                success=True,
                data={
                    "action": "set_temperature",
                    "verified": True,
                    "execution_source": "home_assistant",
                },
                message="Device action completed",
                agent="device",
                task_id=task_id,
                confidence=0.95,
                metadata={
                    "verified": True,
                    "execution_source": "home_assistant",
                },
            )

    class FakeLLMClient:
        async def generate_structured(
            self,
            messages,
            output_model,
            system=None,
            temperature=0.7,
        ):
            return output_model(
                summary="The media room is warm, so use a moderate cooling target.",
                recommended_temperature=78.0,
                rationale="78 F improves comfort without aggressive cooling.",
            )

        async def close(self):
            return None

    async def fake_ha_state(input_data):
        states = {
            demo.DEMO_MEDIA_THERMOSTAT_ENTITY: ToolExecutionResult(
                capability="ha_get_state",
                result={
                    "entity_id": demo.DEMO_MEDIA_THERMOSTAT_ENTITY,
                    "state": "cool",
                    "attributes": {
                        "temperature": 78.0,
                        "current_temperature": 82.0,
                        "current_humidity": 59.0,
                    },
                },
            ),
            demo.DEMO_MEDIA_TEMPERATURE_SENSOR: ToolExecutionResult(
                capability="ha_get_state",
                result={
                    "entity_id": demo.DEMO_MEDIA_TEMPERATURE_SENSOR,
                    "state": "82.4",
                    "attributes": {},
                },
            ),
            demo.DEMO_MEDIA_HUMIDITY_SENSOR: ToolExecutionResult(
                capability="ha_get_state",
                result={
                    "entity_id": demo.DEMO_MEDIA_HUMIDITY_SENSOR,
                    "state": "59",
                    "attributes": {},
                },
            ),
        }
        return states[input_data.entity_id]

    monkeypatch.setattr(demo, "healthcheck_mysql", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "healthcheck_arcadedb", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "ha_get_state_handler", fake_ha_state)
    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(demo, "_demo_orchestrator", FakeOrchestrator())

    response = client.post("/demo/demo2", json={"code": "Demo2"})

    assert response.status_code == 200
    assert "Demo2 started" in response.text
    assert "Media room climate snapshot" in response.text
    assert "Thermostat LLM reasoning" in response.text
    assert "Thermostat action result" in response.text
    assert "Demo2 complete" in response.text
    assert '"agent": "llm"' in response.text
    assert '"agent": "device"' in response.text
    assert '"bounded_temperature": 78.0' in response.text


def test_demo3_streams_sprinkler_action(client, monkeypatch):
    class FakeOrchestrator:
        async def submit(self, task):
            assert task.payload["action"] == "turn_off"
            assert task.payload["domain"] == "switch"
            assert task.payload["entity_id"] == demo.DEMO_SPRINKLER_ENTITY
            return "task-sprinkler-1"

        async def get_result(self, task_id):
            assert task_id == "task-sprinkler-1"
            return Result(
                success=True,
                data={
                    "action": "turn_off",
                    "state": "off",
                    "verified": True,
                    "execution_source": "home_assistant",
                },
                message="Device action completed",
                agent="device",
                task_id=task_id,
                confidence=0.95,
                metadata={
                    "verified": True,
                    "execution_source": "home_assistant",
                },
            )

    class FakeLLMClient:
        async def generate(self, *args, **kwargs):
            return "The watering switch should be turned off to conserve water."

        async def close(self):
            return None

    state = ToolExecutionResult(
        capability="ha_get_state",
        result={"entity_id": demo.DEMO_SPRINKLER_ENTITY, "state": "on"},
    )

    monkeypatch.setattr(demo, "healthcheck_mysql", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "healthcheck_arcadedb", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "ha_get_state_handler", AsyncMock(return_value=state))
    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(demo, "_demo_orchestrator", FakeOrchestrator())

    response = client.post("/demo/demo3", json={"code": "Demo3"})

    assert response.status_code == 200
    assert "Demo3 started" in response.text
    assert "Sprinkler LLM reasoning" in response.text
    assert "Sprinkler action result" in response.text
    assert "Demo3 complete" in response.text


def test_demo4_streams_garage_close_action(client, monkeypatch):
    class FakeOrchestrator:
        async def submit(self, task):
            assert task.payload["action"] == "close"
            assert task.payload["domain"] == "cover"
            assert task.payload["entity_id"] == demo.DEMO_GARAGE_ENTITY
            return "task-garage-1"

        async def get_result(self, task_id):
            assert task_id == "task-garage-1"
            return Result(
                success=True,
                data={
                    "action": "close",
                    "state": "closed",
                    "verified": True,
                    "execution_source": "home_assistant",
                },
                message="Device action completed",
                agent="device",
                task_id=task_id,
                confidence=0.95,
                metadata={
                    "verified": True,
                    "execution_source": "home_assistant",
                },
            )

    class FakeLLMClient:
        async def generate(self, *args, **kwargs):
            return "The garage should be secured with a close-only command."

        async def close(self):
            return None

    state = ToolExecutionResult(
        capability="ha_get_state",
        result={"entity_id": demo.DEMO_GARAGE_ENTITY, "state": "open"},
    )

    monkeypatch.setattr(demo, "healthcheck_mysql", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "healthcheck_arcadedb", AsyncMock(return_value=True))
    monkeypatch.setattr(demo, "ha_get_state_handler", AsyncMock(return_value=state))
    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(demo, "_demo_orchestrator", FakeOrchestrator())

    response = client.post("/demo/demo4", json={"code": "Demo4"})

    assert response.status_code == 200
    assert "Demo4 started" in response.text
    assert "Garage LLM reasoning" in response.text
    assert "Garage action result" in response.text
    assert "Demo4 complete" in response.text


def test_periodic_feedback_returns_suggestions_only(client, monkeypatch):
    class FakeLLMClient:
        async def generate(
            self,
            prompt,
            system=None,
            temperature=0.7,
            max_retries=3,
            stream=False,
        ):
            return (
                "Summary: House appears occupied with a few energy items to review.\n"
                "- Review bedroom computer power because it is the highest current load.\n"
                "- Garage looks secure because garage doors are closed in this snapshot."
            )

        async def close(self):
            return None

    states = [
        {
            "entity_id": "person.econest",
            "state": "home",
            "attributes": {"friendly_name": "econest"},
        },
        {
            "entity_id": "light.bedroom_1_light_1",
            "state": "on",
            "attributes": {"friendly_name": "Bedroom 1 Light"},
        },
        {
            "entity_id": "cover.garage12",
            "state": "closed",
            "attributes": {"friendly_name": "Garage Door"},
        },
        {
            "entity_id": "sensor.sp7_power_minute_average",
            "state": "63.1",
            "attributes": {"friendly_name": "Bedroom 1 Computer Power Minute Average"},
        },
        {
            "entity_id": "sensor.breaker_1_energy_today",
            "state": "19.2",
            "attributes": {"friendly_name": "Input Breaker Energy Today"},
        },
    ]

    monkeypatch.setattr(demo, "_read_all_ha_states", AsyncMock(return_value=states))
    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)

    response = client.get("/demo/feedback")

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "suggestions_only"
    assert data["source"] == "ollama"
    assert data["snapshot"]["occupancy_status"] == "home"
    assert data["suggestions"][0]["category"] == "energy"
    assert "action" not in data


def test_demo_audit_returns_recent_events(client, monkeypatch):
    monkeypatch.setattr(
        demo,
        "read_recent_audit_events",
        lambda limit: [
            {
                "timestamp": "2026-06-14T00:00:00+00:00",
                "event_type": "task.completed",
                "task_id": "task-1",
                "agent": "device",
                "success": True,
            }
        ],
    )

    response = client.get("/demo/audit?limit=500")

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 200
    assert data["events"][0]["event_type"] == "task.completed"
    assert data["events"][0]["agent"] == "device"


def test_demo_audit_summary_returns_metrics(client, monkeypatch):
    monkeypatch.setattr(
        demo,
        "read_recent_audit_events",
        lambda limit: [
            {
                "event_type": "task.completed",
                "task_id": "task-1",
                "agent": "device",
                "success": True,
            },
            {
                "event_type": "task.completed",
                "task_id": "task-2",
                "agent": "device",
                "success": False,
            },
        ],
    )

    response = client.get("/demo/audit/summary?limit=9999")

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 5000
    assert data["summary"]["task_completed_count"] == 2
    assert data["summary"]["task_success_rate"] == 0.5


async def test_autonomous_action_recommendation_uses_allowed_light(monkeypatch):
    class FakeLLMClient:
        async def generate_structured(
            self,
            messages,
            output_model,
            system=None,
            temperature=0.7,
        ):
            return output_model(
                should_act=True,
                confidence=0.93,
                domain="light",
                action="turn_off",
                entity_id=demo.DEMO_MEDIA_LIGHT_ENTITY,
                reason="The allowlisted media room light is on with no active motion.",
                expected_outcome={
                    "entity_id": demo.DEMO_MEDIA_LIGHT_ENTITY,
                    "state": "off",
                },
                risk_level="LOW",
            )

        async def close(self):
            return None

    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        demo.settings,
        "AUTONOMY_ALLOWED_ENTITIES",
        demo.DEMO_MEDIA_LIGHT_ENTITY,
    )
    monkeypatch.setattr(demo.settings, "AUTONOMY_ALLOWED_ACTIONS", "light.turn_off")

    recommendation = await demo.recommend_autonomous_action(
        {
            "snapshot": {
                "lights_on": [
                    {
                        "entity_id": demo.DEMO_MEDIA_LIGHT_ENTITY,
                        "name": "Media Room Light",
                        "state": "on",
                    }
                ],
                "active_motion": [],
            },
            "suggestions": [],
        }
    )

    assert recommendation is not None
    assert recommendation["entity_id"] == demo.DEMO_MEDIA_LIGHT_ENTITY
    assert recommendation["action"] == "turn_off"
    assert recommendation["expected_outcome"]["state"] == "off"

@pytest.mark.anyio
async def test_autonomous_action_recommendation_allows_turn_on_media_light(monkeypatch):
    class FakeLLMClient:
        async def generate_structured(
            self,
            messages,
            output_model,
            system=None,
            temperature=0.7,
        ):
            return output_model(
                should_act=True,
                confidence=0.93,
                domain="light",
                action="turn_on",
                entity_id=demo.DEMO_MEDIA_LIGHT_ENTITY,
                reason="Motion is detected in the media room and the media room light is off.",
                expected_outcome={
                    "entity_id": demo.DEMO_MEDIA_LIGHT_ENTITY,
                    "state": "on",
                },
                risk_level="LOW",
            )

        async def close(self):
            return None

    monkeypatch.setattr(demo, "LLMClient", FakeLLMClient)
    monkeypatch.setattr(
        demo.settings,
        "AUTONOMY_ALLOWED_ENTITIES",
        demo.DEMO_MEDIA_LIGHT_ENTITY,
    )
    monkeypatch.setattr(
        demo.settings,
        "AUTONOMY_ALLOWED_ACTIONS",
        "light.turn_on,light.turn_off",
    )

    recommendation = await demo.recommend_autonomous_action(
        {
            "snapshot": {
                "lights_on": [],
                "active_motion": [
                    {
                        "entity_id": "binary_sensor.media_room_motion",
                        "name": "Media Room Motion",
                        "state": "on",
                    }
                ],
            },
            "suggestions": [],
        }
    )

    assert recommendation is not None
    assert recommendation["entity_id"] == demo.DEMO_MEDIA_LIGHT_ENTITY
    assert recommendation["action"] == "turn_on"
    assert recommendation["expected_outcome"]["state"] == "on"

def test_light_policy_reasoning_guard_replaces_contradiction():
    reasoning = demo._guard_light_policy_reasoning(
        "The media room light should remain on even though no motion was detected.",
        state="on",
        recommended_action="turn_off",
    )

    assert "turn it off" in reasoning
    assert "wasting energy" in reasoning
