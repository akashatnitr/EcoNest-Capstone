"""Tests for the browser demo flow."""

from unittest.mock import AsyncMock

from orchestrator.agents.base import Result
from orchestrator.api import demo
from orchestrator.mcp.models import ToolExecutionResult


def test_demo_page_serves_frontend(client):
    response = client.get("/demo")

    assert response.status_code == 200
    assert "EcoNest Demo Console" in response.text
    assert "Demo1" in response.text


def test_demo1_rejects_invalid_code(client):
    response = client.post("/demo/demo1", json={"code": "wrong"})

    assert response.status_code == 403


def test_demo1_streams_readable_progress(client, monkeypatch):
    class FakeOrchestrator:
        async def submit(self, task):
            if task.payload.get("type") == "energy":
                assert task.metadata["event_type"] == "energy_anomaly"
                return "task-energy-1"
            assert task.payload["action"] == "turn_on"
            assert task.metadata["source"] == "ha_event_replay"
            return "task-device-1"

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
    monkeypatch.setattr(demo, "_demo_orchestrator", FakeOrchestrator())

    response = client.post("/demo/demo1", json={"code": "Demo1"})

    assert response.status_code == 200
    assert "Demo1 started" in response.text
    assert "Infrastructure check" in response.text
    assert "Energy agent result" in response.text
    assert "Device agent result" in response.text
    assert "Demo1 complete" in response.text
    assert '"agent": "energy"' in response.text
    assert '"agent": "device"' in response.text
    assert '"verified": true' in response.text
