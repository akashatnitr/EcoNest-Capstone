"""Tests for the human-readable autonomy activity feed."""

from orchestrator.api.autonomy import _recommendation_view, _recommendation_views


def test_recommendation_view_preserves_human_decision_context() -> None:
    """Expose the timestamp, reason, confidence, and target from audit data."""
    view = _recommendation_view(
        {
            "timestamp": "2026-08-26T19:00:00+00:00",
            "event_type": "autonomy.action.recommended",
            "recommendation": {
                "action": "turn_off",
                "entity_id": "light.upstairs_media_light_1",
                "confidence": 0.9,
                "reason": "No active motion is present.",
                "risk_level": "LOW",
                "source": "fallback_policy",
                "fallback_reason": "Ollama returned an invalid structured response.",
            },
        }
    )

    assert view["timestamp"] == "2026-08-26T19:00:00+00:00"
    assert view["confidence"] == 0.9
    assert view["reason"] == "No active motion is present."
    assert view["entity_id"] == "light.upstairs_media_light_1"
    assert view["fallback_reason"] == "Ollama returned an invalid structured response."


def test_recommendation_view_includes_successful_execution() -> None:
    """Show a completed Home Assistant action next to its recommendation."""
    views = _recommendation_views(
        [
            {
                "timestamp": "2026-08-26T19:00:00+00:00",
                "event_type": "autonomy.action.recommended",
                "recommendation": {
                    "action": "turn_off",
                    "entity_id": "light.upstairs_media_light_1",
                },
            },
            {
                "event_type": "autonomy.action.executed",
                "success": True,
                "recommendation": {
                    "action": "turn_off",
                    "entity_id": "light.upstairs_media_light_1",
                },
                "result": {"success": True},
            },
        ]
    )

    assert views[0]["outcome"]["state"] == "success"
    assert views[0]["outcome"]["label"] == "Executed successfully"


def test_recommendation_view_explains_access_failure() -> None:
    """Distinguish permission failures from ordinary device errors."""
    views = _recommendation_views(
        [
            {
                "event_type": "autonomy.action.recommended",
                "recommendation": {
                    "action": "turn_on",
                    "entity_id": "light.upstairs_media_light_1",
                },
            },
            {
                "event_type": "autonomy.action.executed",
                "success": False,
                "recommendation": {
                    "action": "turn_on",
                    "entity_id": "light.upstairs_media_light_1",
                },
                "result": {"error": "Home Assistant returned 403 Forbidden"},
            },
        ]
    )

    assert views[0]["outcome"]["state"] == "failed"
    assert views[0]["outcome"]["label"] == "Failed — Home Assistant access limitation"
