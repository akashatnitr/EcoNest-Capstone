"""Tests for the model-assisted Command Center flow."""

from unittest.mock import AsyncMock

from orchestrator.api import command


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
    assert "energy or security recommendation" in response.text
    assert "timeout_seconds: timeoutSeconds" in response.text
    assert 'id="entityId"' not in response.text
    assert 'id="action"' not in response.text


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
    resource_read.assert_awaited_once()
    assert resource_read.await_args.kwargs["agent"] == "command_interpreter"


def test_interpret_command_rejects_hallucinated_entity(
    client, override_current_user, monkeypatch
):
    """A model must not propose a device that was not in the MCP inventory."""
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
    response = client.post("/command/interpret", json={"intent": "Turn off light"})

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["entity_id"] is None


def test_interpret_command_confirms_energy_recommendation(
    client, override_current_user, monkeypatch
):
    """Energy questions stay advisory and do not require a device target."""
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
    response = client.post(
        "/command/interpret",
        json={"intent": "How can I reduce my home's energy use?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmation_required"
    assert data["request_kind"] == "energy_recommendation"
    assert data["entity_id"] is None


def test_interpret_command_confirms_security_recommendation(
    client, override_current_user, monkeypatch
):
    """Security questions stay advisory and do not require a device target."""
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
    response = client.post(
        "/command/interpret",
        json={"intent": "Give me a home security recommendation"},
    )

    assert response.status_code == 200
    assert response.json()["request_kind"] == "security_recommendation"


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
    response = client.post(
        "/command/interpret",
        json={"intent": "Recommend ways to save energy"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmation_required"


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
