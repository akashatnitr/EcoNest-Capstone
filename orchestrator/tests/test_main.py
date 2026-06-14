"""Tests for application-level status endpoints."""

from orchestrator import main


def test_autonomy_status_reports_disabled_monitor(client, monkeypatch):
    monkeypatch.setattr(main, "autonomous_monitor", None)
    monkeypatch.setattr(main.settings, "AUTONOMY_MONITOR_ENABLED", False)
    monkeypatch.setattr(main.settings, "AUTONOMY_MONITOR_INTERVAL_SECONDS", 300)
    monkeypatch.setattr(main.settings, "AUTONOMY_MONITOR_RUN_ON_STARTUP", True)
    monkeypatch.setattr(main.settings, "AUTONOMY_ACTIONS_ENABLED", False)

    response = client.get("/autonomy/status")

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["running"] is False
    assert data["actions_enabled"] is False
