"""Tests for background autonomous monitoring."""

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
