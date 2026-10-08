"""Autonomy activity page and recommendation history API."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from orchestrator.agents.base import Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.config import get_settings
from orchestrator.core.database import mysql_session_context
from orchestrator.core.google_calendar import (
    claim_active_calendar_context_reviews,
    connected_calendar_user_ids,
    sync_calendar_context,
)
from orchestrator.core.audit import read_recent_audit_events_async
from orchestrator.core.permissions import Role

router = APIRouter(tags=["autonomy"])
_energy_orchestrator = AgentOrchestrator()
settings = get_settings()
SCHEDULED_RECOMMENDATION_KINDS = ("energy", "security", "irrigation")


class EnergyRecommendationRequest(BaseModel):
    """Input for a resident-requested, recommendation-only energy review."""

    intent: str = "Review my home's energy use and recommend better timing."
    payload: dict[str, Any] = Field(default_factory=dict)


class AdvisoryRecommendationRequest(BaseModel):
    """Optional caller context for an advisory review."""

    intent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


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
    events = await read_recent_audit_events_async(
        settings.AUTONOMY_RECOMMENDATION_HISTORY_EVENTS
    )
    recommendations = list(reversed(_recommendation_views(events)))[:bounded_limit]
    return {
        "recommendations": recommendations,
        "limit": bounded_limit,
    }


@router.post("/autonomy/energy-recommendations")
async def request_energy_recommendations(
    request: EnergyRecommendationRequest,
) -> dict[str, str]:
    """Queue an on-demand advisory energy review for the activity feed."""
    task_id = await _submit_advisory_review("energy", request.intent, request.payload)
    return {"task_id": task_id, "status": "submitted"}


@router.post("/autonomy/security-recommendations")
async def request_security_recommendations(
    request: AdvisoryRecommendationRequest,
) -> dict[str, str]:
    """Queue an on-demand advisory security review without device control."""
    task_id = await _submit_advisory_review(
        "security",
        request.intent or "Review the home for security recommendations.",
        request.payload,
    )
    return {"task_id": task_id, "status": "submitted"}


@router.post("/autonomy/watering-recommendations")
async def request_watering_recommendations(
    request: AdvisoryRecommendationRequest,
) -> dict[str, str]:
    """Queue an on-demand advisory watering review without valve control."""
    task_id = await _submit_advisory_review(
        "irrigation",
        request.intent
        or "Review weather and irrigation conditions for watering recommendations.",
        request.payload,
    )
    return {"task_id": task_id, "status": "submitted"}


@router.get("/autonomy/energy-recommendations/{task_id}")
async def energy_recommendation_status(task_id: str) -> dict[str, Any]:
    """Return the status of a requested energy review."""
    return await _advisory_recommendation_status(task_id)


@router.get("/autonomy/recommendation-tasks/{task_id}")
async def recommendation_task_status(task_id: str) -> dict[str, Any]:
    """Return the status of any on-demand advisory review."""
    return await _advisory_recommendation_status(task_id)


async def _advisory_recommendation_status(task_id: str) -> dict[str, Any]:
    """Read the status of an in-memory advisory task."""
    result = await _energy_orchestrator.get_result(task_id)
    if result is None:
        return {"task_id": task_id, "status": "running"}
    return {
        "task_id": task_id,
        "status": "completed" if result.success else "failed",
        "result": result.data,
        "message": result.message,
    }


async def _submit_advisory_review(
    kind: str,
    intent: str,
    payload: dict[str, Any],
) -> str:
    """Submit a user-requested review directly to its advisory-only agent."""
    return await _energy_orchestrator.submit(
        Task(
            intent=intent,
            payload={
                **payload,
                "type": kind,
                "use_llm": bool(payload.get("use_llm", kind == "energy")),
            },
            timeout_seconds=settings.OLLAMA_TIMEOUT_SECONDS,
            metadata={
                "source": "http_api",
                "user_role": Role.HOMEOWNER.value,
                "routed_agent": kind,
                "recommendation_kind": kind,
            },
        )
    )


async def submit_calendar_preview_reviews(
    calendar_context: dict[str, Any],
) -> list[dict[str, str]]:
    """Run calendar-triggered advisory reviews without granting device control."""
    context_mode = str(calendar_context.get("mode") or "normal")
    if context_mode not in {"away", "hosting"}:
        return []
    review_payload = {
        "calendar_context": calendar_context,
        "use_llm": True,
        "recommendation_only": True,
    }
    reviews: list[dict[str, str]] = []
    for kind, intent in (
        ("energy", f"Review energy recommendations for a household that is {context_mode}."),
        ("security", f"Review security recommendations for a household that is {context_mode}."),
    ):
        task_id = await _energy_orchestrator.submit(
            Task(
                intent=intent,
                payload={**review_payload, "type": kind},
                timeout_seconds=settings.OLLAMA_TIMEOUT_SECONDS,
                metadata={
                    "source": "calendar_context",
                    "event_type": "calendar_context_detected",
                    "user_role": Role.HOMEOWNER.value,
                    "routed_agent": kind,
                    "recommendation_kind": kind,
                },
            )
        )
        reviews.append({"kind": kind, "task_id": task_id})
    return reviews


async def run_calendar_context_reviews() -> dict[str, int]:
    """Synchronize connected calendars and queue one advisory review per active event."""
    synced = 0
    queued = 0
    async with mysql_session_context() as session:
        user_ids = await connected_calendar_user_ids(session)
        for user_id in user_ids:
            result = await sync_calendar_context(session, settings, user_id)
            if not result.get("synced"):
                continue
            synced += 1
            for context in await claim_active_calendar_context_reviews(session, user_id):
                queued += len(await submit_calendar_preview_reviews(context))
    return {"synced_calendars": synced, "queued_reviews": queued}


def scheduled_recommendation_kind(now: datetime | None = None) -> str:
    """Select energy, security, then watering in repeating ten-minute slots."""
    local_now = now or datetime.now()
    slot = (local_now.hour * 6) + (local_now.minute // 10)
    return SCHEDULED_RECOMMENDATION_KINDS[slot % len(SCHEDULED_RECOMMENDATION_KINDS)]


async def run_scheduled_recommendation() -> dict[str, Any]:
    """Run the advisory review assigned to the current ten-minute schedule slot."""
    kind = scheduled_recommendation_kind()
    task_id = await _energy_orchestrator.submit(
        Task(
            intent={
                "energy": "Review household energy use and recommend better timing.",
                "security": "Review the home for security recommendations.",
                "irrigation": "Review weather and irrigation conditions for watering recommendations.",
            }[kind],
            payload={"type": kind, "use_llm": kind == "energy"},
            timeout_seconds=settings.OLLAMA_TIMEOUT_SECONDS,
            metadata={
                "source": "background_monitor",
                "user_role": Role.HOMEOWNER.value,
                "routed_agent": kind,
                "recommendation_kind": kind,
            },
        )
    )
    result = await _wait_for_recommendation(task_id)
    return {
        "kind": kind,
        "task_id": task_id,
        "success": result.success if result is not None else False,
        "message": result.message if result is not None else "Recommendation timed out",
    }


async def _wait_for_recommendation(task_id: str) -> Any | None:
    """Wait only for the bounded scheduled advisory task to finish."""
    checks = max(1, settings.OLLAMA_TIMEOUT_SECONDS * 2)
    for _ in range(checks):
        result = await _energy_orchestrator.get_result(task_id)
        if result is not None:
            return result
        await asyncio.sleep(0.5)
    return None


def _recommendation_view(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize audit data into a stable, human-facing recommendation record."""
    recommendation = event.get("recommendation")
    data = recommendation if isinstance(recommendation, dict) else {}
    return {
        "timestamp": event.get("timestamp"),
        "action": data.get("action") or event.get("action") or "No action",
        "entity_id": data.get("entity_id")
        or event.get("entity_id")
        or "Unknown target",
        "confidence": data.get("confidence", event.get("confidence")),
        "reason": data.get("reason") or "No explanation was recorded.",
        "risk_level": data.get("risk_level") or "Unknown",
        "should_act": bool(data.get("should_act", True)),
        "source": data.get("source") or "autonomy monitor",
        "fallback_reason": data.get("fallback_reason"),
        "technical_details": _technical_details(event),
    }


def _recommendation_views(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair recommendation audit records with their later execution outcome."""
    views: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if event.get("event_type") == "autonomy.action.recommended":
            view = _recommendation_view(event)
            view["outcome"] = _recommendation_outcome(events, index, view)
            views.append(view)
        elif str(event.get("event_type", "")).endswith(".recommendations.generated"):
            views.extend(_advisory_recommendation_views(event))
    return views


def _advisory_recommendation_views(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert an advisory agent review into one timestamped card per recommendation."""
    recommendations = event.get("recommendations")
    if not isinstance(recommendations, list):
        return []
    views: list[dict[str, Any]] = []
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        category = _advisory_category(event)
        reason = recommendation.get("reasoning") or "No explanation was recorded."
        if category == "Watering":
            reason = _watering_reason_for_display(str(reason))
        views.append(
            {
                "timestamp": event.get("timestamp"),
                "action": recommendation.get("action") or "Household review",
                "entity_id": _advisory_target(event),
                "category": category,
                "reason": reason,
                "risk_level": recommendation.get("priority") or "Unknown",
                "should_act": False,
                "source": event.get("source") or "advisory review",
                "fallback_reason": None,
                "technical_details": _technical_details(event),
                "outcome": {
                    "state": "advisory",
                    "label": "Recommendation only",
                    "detail": "EcoNest did not control any device.",
                },
            }
        )
    return views


def _technical_details(event: dict[str, Any]) -> dict[str, Any]:
    """Return bounded trigger and state facts retained with a recommendation."""
    details = event.get("technical_details")
    return details if isinstance(details, dict) else {}


def _advisory_category(event: dict[str, Any]) -> str:
    """Return the resident-facing category for a stored advisory event."""
    event_type = str(event.get("event_type", ""))
    return {
        "energy.recommendations.generated": "Energy",
        "security.recommendations.generated": "Security",
        "irrigation.recommendations.generated": "Watering",
    }.get(event_type, "Household")


def _advisory_target(event: dict[str, Any]) -> str:
    """Return a plain-language target for an advisory category."""
    return {
        "Energy": "Household energy",
        "Security": "Home security",
        "Watering": "Irrigation and weather",
        "Household": "Household review",
    }[_advisory_category(event)]


def _watering_reason_for_display(reason: str) -> str:
    """Keep retained watering cards concise as the explanation evolves."""
    legacy_reason = (
        "No material rain is forecast in the next 24 hours, but EcoNest does not yet "
        "have reliable soil-moisture or zone-runtime evidence. It will not recommend "
        "turning irrigation on automatically."
    )
    if reason == legacy_reason:
        return (
            "No material rain is forecast in the next 24 hours. "
            "EcoNest recommends reviewing the watering schedule before running zones."
        )
    return reason


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


def _matches_recommendation(
    event: dict[str, Any], recommendation: dict[str, Any]
) -> bool:
    """Check whether an action result belongs to the given recommendation."""
    event_recommendation = event.get("recommendation")
    if not isinstance(event_recommendation, dict):
        return False
    return event_recommendation.get("action") == recommendation.get(
        "action"
    ) and event_recommendation.get("entity_id") == recommendation.get("entity_id")


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
    if any(
        term in normalized
        for term in ("401", "403", "access", "permission", "unauthorized", "forbidden")
    ):
        label = "Failed — Home Assistant access limitation"
    else:
        label = "Failed — device action error"
    return {"state": "failed", "label": label, "detail": detail}
