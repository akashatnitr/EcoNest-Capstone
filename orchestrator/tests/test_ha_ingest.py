"""Tests for adaptive-learning state selection during HA ingestion."""

from orchestrator.config import Settings
from orchestrator.core.ha_ingest import HomeAssistantIngestor, _is_sensor_state, _room_environments


def test_adaptive_ingestion_keeps_climate_weather_and_irrigation_states():
    assert _is_sensor_state({"entity_id": "climate.media_room", "state": "cool"})
    assert _is_sensor_state({"entity_id": "weather.forecast_home", "state": "sunny"})
    assert _is_sensor_state({"entity_id": "valve.front_lawn", "state": "closed"})
    assert _is_sensor_state({"entity_id": "switch.front_lawn_watering", "state": "off"})
    assert not _is_sensor_state({"entity_id": "switch.garage", "state": "off"})
    assert not _is_sensor_state({"entity_id": "climate.media_room", "state": "unavailable"})


def test_room_environments_uses_temperature_and_humidity_states_without_registry():
    environments = _room_environments(
        [
            {
                "entity_id": "sensor.temperature",
                "state": "72.4",
                "attributes": {"device_class": "temperature"},
            },
            {
                "entity_id": "sensor.humidity",
                "state": "45",
                "attributes": {"device_class": "humidity"},
            },
        ],
        None,
    )

    assert environments == {"home_assistant": {"temperature": 72.4, "humidity": 45.0}}


def test_ingestor_waits_for_a_prior_state_before_emitting_events():
    ingestor = HomeAssistantIngestor(Settings())
    initial = {"entity_id": "binary_sensor.garage_motion", "state": "off", "last_updated": "one"}
    changed = {"entity_id": "binary_sensor.garage_motion", "state": "on", "last_updated": "two"}

    assert ingestor._event_transitions([initial]) == []
    ingestor._last_event_states["binary_sensor.garage_motion"] = initial
    assert ingestor._event_transitions([changed]) == [(initial, changed)]
