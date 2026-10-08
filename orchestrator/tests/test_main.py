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


def test_autonomy_run_once_reports_disabled_monitor(client, monkeypatch):
    monkeypatch.setattr(main, "autonomous_monitor", None)

    response = client.post("/autonomy/run-once")

    assert response.status_code == 409


def test_autonomy_page_exposes_the_disabled_execution_stage(client) -> None:
    """The activity UI must not imply disabled autonomous actions were executed."""
    response = client.get("/autonomy")

    assert response.status_code == 200
    assert "Autonomous decision pipeline" in response.text
    assert "Autonomous device actions disabled" in response.text
    assert "Execute and verify" in response.text
    assert 'fetch("/autonomy/status")' in response.text


def test_autonomy_page_can_hide_and_restore_recommendation_history(client) -> None:
    """Clearing the visible feed is reversible and does not delete audit data."""
    response = client.get("/autonomy")

    assert response.status_code == 200
    assert "Hide current history" in response.text
    assert "Restore hidden history" in response.text
    assert "econest_autonomy_history_cutoff" in response.text


def test_autonomy_page_exposes_recommendation_technical_details(client) -> None:
    """Cards should make retained trigger and state evidence inspectable."""
    response = client.get("/autonomy")

    assert response.status_code == 200
    assert "Technical details" in response.text
    assert "What triggered this review" in response.text
    assert "State before event" in response.text
    assert "State after event" in response.text


def test_shared_pastel_theme_is_served(client) -> None:
    response = client.get("/static/econest-theme.css")

    assert response.status_code == 200
    assert "Shared EcoNest neutral earth interface" in response.text
