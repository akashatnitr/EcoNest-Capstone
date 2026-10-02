"""Focused tests for raw-reading event reconstruction helpers."""

from datetime import datetime

from orchestrator.core.home_events_backfill import _state_from_reading


def test_state_from_reading_restores_home_assistant_power_state() -> None:
    state = _state_from_reading(
        {
            "ha_entity_id": "sensor.dryer_power",
            "timestamp": datetime(2026, 9, 30, 16, 40, 42),
            "data": {
                "state": "0",
                "attributes": {"device_class": "power", "unit_of_measurement": "W"},
                "last_updated": "2026-09-30T16:40:41Z",
            },
        }
    )

    assert state == {
        "entity_id": "sensor.dryer_power",
        "state": "0",
        "attributes": {"device_class": "power", "unit_of_measurement": "W"},
        "last_changed": "2026-09-30T16:40:42",
        "last_updated": "2026-09-30T16:40:41Z",
    }


def test_state_from_reading_rejects_invalid_json() -> None:
    assert _state_from_reading({"ha_entity_id": "sensor.dryer_power", "data": "not-json"}) is None
