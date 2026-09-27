"""Tests for retained Home Assistant irrigation history parsing."""

from datetime import datetime

from orchestrator.core.irrigation import _completed_runs


def test_completed_runs_pairs_on_and_off_history() -> None:
    entity_id = "switch.front_l_manual_watering"
    payload = [
        [
            {
                "entity_id": entity_id,
                "state": "off",
                "last_changed": "2026-09-14T10:00:00+00:00",
            },
            {
                "entity_id": entity_id,
                "state": "on",
                "last_changed": "2026-09-14T10:05:00+00:00",
            },
            {
                "entity_id": entity_id,
                "state": "off",
                "last_changed": "2026-09-14T10:20:00+00:00",
            },
        ]
    ]

    runs = _completed_runs(payload, [entity_id])

    assert len(runs) == 1
    assert runs[0]["entity_id"] == entity_id
    assert runs[0]["zone_name"] == "Front L"
    assert runs[0]["started_at"] == datetime(2026, 9, 14, 10, 5)
    assert runs[0]["ended_at"] == datetime(2026, 9, 14, 10, 20)
    assert runs[0]["duration_seconds"] == 900
