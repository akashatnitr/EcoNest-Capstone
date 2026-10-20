"""Tests for the canonical Home Snapshot."""

from unittest.mock import AsyncMock

import pytest

from orchestrator.api import home
from orchestrator.core.home_snapshot import build_home_snapshot
from orchestrator.mcp import resources


def test_build_home_snapshot_summarizes_home_state():
    states = [
        {
            "entity_id": "person.alice",
            "state": "home",
            "attributes": {"friendly_name": "Alice"},
        },
        {
            "entity_id": "device_tracker.ipad",
            "state": "home",
            "attributes": {"friendly_name": "iPad"},
        },
        {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "Living Room Light"},
        },
        {
            "entity_id": "light.upstairs_media_light_1",
            "state": "on",
            "attributes": {"friendly_name": "Media Room Lights Light 1"},
        },
        {
            "entity_id": "switch.coffee_machine",
            "state": "on",
            "attributes": {"friendly_name": "Coffee Machine"},
        },
        {
            "entity_id": "cover.garage",
            "state": "open",
            "attributes": {"friendly_name": "Garage Door"},
        },
        {
            "entity_id": "binary_sensor.front_motion",
            "state": "on",
            "attributes": {"friendly_name": "Front Motion"},
        },
        {
            "entity_id": "climate.living_room",
            "state": "cool",
            "attributes": {"friendly_name": "Living Room", "current_temperature": 76},
        },
        {
            "entity_id": "climate.media_room",
            "state": "cool",
            "attributes": {"friendly_name": "Media Room", "current_temperature": 78},
        },
        {
            "entity_id": "sensor.kitchen_power_minute_average",
            "state": "125.5",
            "attributes": {"friendly_name": "Kitchen Power"},
        },
        {
            "entity_id": "sensor.kitchen_energy_today",
            "state": "4.2",
            "attributes": {"friendly_name": "Kitchen Energy"},
        },
    ]

    snapshot = build_home_snapshot(states)

    assert snapshot["occupancy_status"] == "home"
    assert snapshot["people"][0]["name"] == "Alice"
    assert snapshot["tracked_devices"][0]["name"] == "iPad"
    assert snapshot["lights_on"][0]["name"] == "Living Room Light"
    assert snapshot["switches_on"][0]["name"] == "Coffee Machine"
    assert snapshot["covers_open"][0]["name"] == "Garage Door"
    assert snapshot["active_motion"][0]["name"] == "Front Motion"
    assert snapshot["inferred_room_activity"] == [
        {
            "entity_id": "light.upstairs_media_light_1",
            "name": "Media Room",
            "state": "active now · inferred from dimmer light (10-minute timeout)",
            "source": "dimmer_light",
        }
    ]
    assert snapshot["inferred_activity_timeout_minutes"] == 10
    assert snapshot["current_indoor_temperature_f"] == 77.0
    assert snapshot["temperature_readings"] == [
        {"entity_id": "climate.living_room", "name": "Living Room", "state": 76.0},
        {"entity_id": "climate.media_room", "name": "Media Room", "state": 78.0},
    ]
    assert snapshot["top_power_now_w"][0]["value"] == 125.5
    assert snapshot["top_energy_today_kwh"][0]["value"] == 4.2


def test_build_home_snapshot_reports_away_when_all_people_are_away():
    states = [
        {
            "entity_id": "person.alice",
            "state": "not_home",
            "attributes": {"friendly_name": "Alice"},
        },
        {
            "entity_id": "person.bob",
            "state": "away",
            "attributes": {"friendly_name": "Bob"},
        },
    ]

    snapshot = build_home_snapshot(states)

    assert snapshot["occupancy_status"] == "away"


def test_build_home_snapshot_reports_unknown_without_people():
    snapshot = build_home_snapshot([])

    assert snapshot["occupancy_status"] == "unknown"
    assert snapshot["current_indoor_temperature_f"] is None
    assert snapshot["temperature_readings"] == []
    assert snapshot["people"] == []
    assert snapshot["tracked_devices"] == []
    assert snapshot["lights_on"] == []
    assert snapshot["switches_on"] == []


@pytest.mark.asyncio
async def test_home_snapshot_api_returns_canonical_snapshot(monkeypatch):
    snapshot = {
        "type": "snapshot",
        "available": True,
        "occupancy_status": "home",
        "lights_on": [{"name": "Living Room Light"}],
    }
    monkeypatch.setattr(home, "fetch_home_snapshot", AsyncMock(return_value=snapshot))

    result = await home.home_snapshot()

    assert result["type"] == "snapshot"
    assert result["available"] is True
    assert result["occupancy_status"] == "home"
    assert result["lights_on"][0]["name"] == "Living Room Light"


@pytest.mark.asyncio
async def test_mcp_home_snapshot_returns_same_canonical_snapshot(monkeypatch):
    states = [
        {
            "entity_id": "person.alice",
            "state": "home",
            "attributes": {"friendly_name": "Alice"},
        },
        {
            "entity_id": "switch.coffee_machine",
            "state": "on",
            "attributes": {"friendly_name": "Coffee Machine"},
        },
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return states

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(resources.settings, "HA_TOKEN", "test-token")
    monkeypatch.setattr(resources.settings, "HA_URL", "http://homeassistant")
    monkeypatch.setattr(
        resources.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    result = await resources.home_snapshot_resource()

    expected = build_home_snapshot(states)

    assert result["type"] == "snapshot"
    assert result["available"] is True
    assert {
        key: value
        for key, value in result.items()
        if key not in {"type", "available"}
    } == expected
