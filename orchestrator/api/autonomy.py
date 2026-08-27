"""Autonomy activity page and recommendation history API."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.core.audit import read_recent_audit_events

router = APIRouter(tags=["autonomy"])


@router.get("/autonomy", response_class=HTMLResponse)
async def autonomy_page() -> HTMLResponse:
    """Serve the authenticated autonomy activity page."""
    page = Path(__file__).resolve().parents[1] / "static" / "autonomy.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/autonomy/recommendations")
async def autonomy_recommendations(
    limit: int = 100,
) -> dict[str, Any]:
    """Return recent autonomous recommendations for the read-only activity page."""
    bounded_limit = max(1, min(limit, 200))
    events = read_recent_audit_events(1000)
    recommendations = list(reversed(_recommendation_views(events)))[:bounded_limit]
    return {
        "recommendations": recommendations,
        "limit": bounded_limit,
    }


def _recommendation_view(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize audit data into a stable, human-facing recommendation record."""
    recommendation = event.get("recommendation")
    data = recommendation if isinstance(recommendation, dict) else {}
    return {
        "timestamp": event.get("timestamp"),
        "action": data.get("action") or event.get("action") or "No action",
        "entity_id": data.get("entity_id") or event.get("entity_id") or "Unknown target",
        "confidence": data.get("confidence", event.get("confidence")),
        "reason": data.get("reason") or "No explanation was recorded.",
        "risk_level": data.get("risk_level") or "Unknown",
        "should_act": bool(data.get("should_act", True)),
        "source": data.get("source") or "autonomy monitor",
        "fallback_reason": data.get("fallback_reason"),
    }


def _recommendation_views(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair recommendation audit records with their later execution outcome."""
    views: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event.get("event_type") != "autonomy.action.recommended":
            continue
        view = _recommendation_view(event)
        view["outcome"] = _recommendation_outcome(events, index, view)
        views.append(view)
    return views


def _recommendation_outcome(
    events: list[dict[str, Any]],
    recommendation_index: int,
    recommendation: dict[str, Any],
) -> dict[str, str]:
    """Return the execution or safeguard outcome for one recommendation."""
    for event in events[recommendation_index + 1 :]:
        event_type = event.get("event_type")
        if event_type == "autonomy.action.recommended":
            break
        if event_type not in {"autonomy.action.executed", "autonomy.action.skipped"}:
            continue
        if not _matches_recommendation(event, recommendation):
            continue
        if event_type == "autonomy.action.skipped":
            return _skipped_outcome(event)
        return _executed_outcome(event)
    return {
        "state": "pending",
        "label": "Awaiting execution result",
        "detail": "EcoNest recorded the recommendation and is awaiting its next step.",
    }


def _matches_recommendation(event: dict[str, Any], recommendation: dict[str, Any]) -> bool:
    """Check whether an action result belongs to the given recommendation."""
    event_recommendation = event.get("recommendation")
    if not isinstance(event_recommendation, dict):
        return False
    return (
        event_recommendation.get("action") == recommendation.get("action")
        and event_recommendation.get("entity_id") == recommendation.get("entity_id")
    )


def _skipped_outcome(event: dict[str, Any]) -> dict[str, str]:
    """Translate a safeguard skip into a resident-friendly outcome."""
    reason = str(event.get("reason", ""))
    if reason == "confidence_below_threshold":
        threshold = event.get("threshold", 0.85)
        return {
            "state": "skipped",
            "label": "Not executed — confidence too low",
            "detail": f"The recommendation did not meet the {float(threshold) * 100:.0f}% confidence threshold.",
        }
    if reason == "actions_disabled":
        return {
            "state": "skipped",
            "label": "Not executed — autonomous actions disabled",
            "detail": "EcoNest recorded the recommendation but was configured not to control devices.",
        }
    if reason == "no_action_executor":
        return {
            "state": "skipped",
            "label": "Not executed — device service unavailable",
            "detail": "EcoNest could not reach its device-action service.",
        }
    return {
        "state": "skipped",
        "label": "Not executed",
        "detail": "EcoNest's safety checks prevented this action.",
    }


def _executed_outcome(event: dict[str, Any]) -> dict[str, str]:
    """Translate a device-action result into a resident-friendly outcome."""
    if event.get("success") is True:
        return {
            "state": "success",
            "label": "Executed successfully",
            "detail": "Home Assistant accepted the requested device action.",
        }

    result = event.get("result")
    result_data = result if isinstance(result, dict) else {}
    detail = str(
        result_data.get("message")
        or result_data.get("error")
        or result_data.get("error_message")
        or "The device action did not complete successfully."
    )
    normalized = detail.lower()
    if any(term in normalized for term in ("401", "403", "access", "permission", "unauthorized", "forbidden")):
        label = "Failed — Home Assistant access limitation"
    else:
        label = "Failed — device action error"
    return {"state": "failed", "label": label, "detail": detail}
