"""Tests for background autonomous monitoring."""

import pytest

from orchestrator.core import autonomy
from orchestrator.core.autonomy import AutonomousMonitor


async def test_autonomous_monitor_run_once_records_success(monkeypatch):
    events = []

    async def collect_feedback():
        return {
            "source": "ollama",
            "suggestions": [{"title": "Review load"}],
            "snapshot": {"occupancy_status": "home"},
        }

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
    )
    result = await monitor.run_once()

    assert result is not None
    assert monitor.status()["success_count"] == 1
    assert events == [
        (
            "autonomy.monitor.completed",
            {
                "success": True,
                "source": "ollama",
                "suggestion_count": 1,
                "occupancy_status": "home",
            },
        )
    ]


async def test_autonomous_monitor_run_once_records_failure(monkeypatch):
    events = []

    async def collect_feedback():
        raise RuntimeError("Home Assistant unavailable")

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
    )
    result = await monitor.run_once()

    assert result is None
    assert monitor.status()["failure_count"] == 1
    assert monitor.status()["last_error"] == "Home Assistant unavailable"
    assert events[0][0] == "autonomy.monitor.failed"
    assert events[0][1]["success"] is False
    assert events[0][1]["error"] == "RuntimeError"


async def test_autonomous_monitor_executes_high_confidence_action(monkeypatch):
    events = []
    executed = []

    async def collect_feedback():
        return {
            "source": "ollama",
            "suggestions": [{"title": "Media room light should be off"}],
            "snapshot": {"occupancy_status": "home"},
        }

    async def recommend_action(feedback):
        return {
            "confidence": 0.95,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
            "expected_outcome": {
                "entity_id": "light.upstairs_media_light_1",
                "state": "off",
            },
        }

    async def execute_action(recommendation):
        executed.append(recommendation)
        return {"success": True, "agent": "device"}

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        action_confidence_threshold=0.85,
        actions_enabled=True,
    )

    await monitor.run_once()

    assert len(executed) == 1
    assert monitor.status()["action_execution_count"] == 1
    assert [event[0] for event in events] == [
        "autonomy.monitor.completed",
        "autonomy.action.recommended",
        "autonomy.action.executed",
    ]


async def test_autonomous_monitor_skips_low_confidence_action(monkeypatch):
    events = []

    async def collect_feedback():
        return {"source": "ollama", "suggestions": [], "snapshot": {}}

    async def recommend_action(feedback):
        return {
            "confidence": 0.5,
            "entity_id": "light.upstairs_media_light_1",
            "domain": "light",
            "action": "turn_off",
        }

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)

    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_confidence_threshold=0.85,
    )

    await monitor.run_once()

    assert monitor.status()["action_skip_count"] == 1
    assert events[-1][0] == "autonomy.action.skipped"
    assert events[-1][1]["reason"] == "confidence_below_threshold"


@pytest.mark.asyncio
async def test_autonomous_monitor_records_but_does_not_execute_when_disabled(monkeypatch):
    """Observe-only mode must retain recommendations without device control."""
    events = []
    executed = []

    async def collect_feedback():
        return {"source": "ollama", "suggestions": [], "snapshot": {}}

    async def recommend_action(feedback):
        return {"confidence": 0.95, "entity_id": "light.safe", "action": "turn_off"}

    async def execute_action(recommendation):
        executed.append(recommendation)
        return {"success": True}

    async def fake_write(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(autonomy, "write_audit_event_async", fake_write)
    monitor = AutonomousMonitor(
        collect_feedback,
        interval_seconds=1,
        action_recommender=recommend_action,
        action_executor=execute_action,
        actions_enabled=False,
    )

    await monitor.run_once()

    assert executed == []
    assert monitor.status()["action_skip_count"] == 1
    assert events[-1][1]["reason"] == "actions_disabled"
