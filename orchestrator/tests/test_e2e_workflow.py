import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.llm.client import LLMClient
from orchestrator.mcp.tools.ha_tools import (
    HAGetStateInput,
    ha_get_state_handler,
)


@pytest.mark.anyio
async def test_full_device_monitoring_flow(
    client,
    override_mysql_session,
    override_current_user,
):
    """
    End-to-end workflow test:

    register -> device control -> agent task
    """

    # ==========================================
    # MOCK DATABASE RESPONSES
    # ==========================================

    mock_session = override_mysql_session

    mock_result = MagicMock()

    mock_result.mappings.return_value.all.return_value = [
        {
            "id": 1,
            "name": "Living Room Lamp",
            "device_type": "SmartBulb",
            "room_id": 1,
            "is_active": True,
        }
    ]

    mock_session.execute = AsyncMock(return_value=mock_result)
    # ==========================================
    # STEP 1 — LIST DEVICES
    # ==========================================

    resp = client.get("/devices")

    assert resp.status_code == 200

    # ==========================================
    # STEP 2 — TURN DEVICE ON
    # ==========================================

    resp = client.post("/devices/1/on")

    assert resp.status_code == 200

    data = resp.json()

    assert data["state"] == "on"

    # ==========================================
    # STEP 3 — MOCK AGENT ORCHESTRATOR
    # ==========================================

    with patch(
        "orchestrator.api.mcp._orchestrator.submit",
        new=AsyncMock(return_value="task-123"),
    ):

        task_payload = {
            "intent": "monitor_energy",
            "payload": {
                "device_id": 1,
                "power": 1200,
            },
        }

        resp = client.post(
            "/mcp/task",
            json=task_payload,
        )

        assert resp.status_code == 202

        body = resp.json()

        assert body["task_id"] == "task-123"

        assert body["status"] == "submitted"


@pytest.mark.anyio
async def test_mocked_homeassistant_state():

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "entity_id": "light.living_room",
        "state": "on",
        "attributes": {"brightness": 255},
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with (
        patch.dict(os.environ, {"HA_TOKEN": "fake-token"}),
        patch("httpx.AsyncClient") as mock_async_client,
    ):

        mock_async_client.return_value.__aenter__.return_value = mock_client

        result = await ha_get_state_handler(
            HAGetStateInput(entity_id="light.living_room")
        )

        assert result["state"] == "on"

        assert result["attributes"]["brightness"] == 255


@pytest.mark.anyio
async def test_mocked_ollama_response():

    mock_response = MagicMock()

    mock_response.json.return_value = {
        "response": (
            "High energy usage detected. " "Recommend turning off idle devices."
        )
    }

    mock_response.raise_for_status.return_value = None

    mock_post = AsyncMock(return_value=mock_response)

    with patch(
        "httpx.AsyncClient.post",
        new=mock_post,
    ):

        client = LLMClient()

        result = await client.generate(
            prompt="Analyze energy spike",
        )

        assert "High energy usage" in result
