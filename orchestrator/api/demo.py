"""Professor-facing live demo routes."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from orchestrator.agents.base import Result, Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.config import get_settings
from orchestrator.core.audit import (
    read_recent_audit_events,
    summarize_audit_events,
    write_audit_event_async,
)
from orchestrator.core.database import healthcheck_arcadedb, healthcheck_mysql
from orchestrator.core.permissions import Role
from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage
from orchestrator.mcp.tools.ha_tools import HAGetStateInput, ha_get_state_handler

router = APIRouter(prefix="/demo", tags=["demo"])
settings = get_settings()
_demo_orchestrator = AgentOrchestrator()
_feedback_memory: dict[str, Any] = {
    "last_occupancy": None,
    "transitions": [],
    "observation_count": 0,
}

DEMO_ACCESS_CODE = "Demo1"
DEMO2_ACCESS_CODE = "Demo2"
DEMO_MEDIA_LIGHT_ENTITY = "light.upstairs_media_light_1"
DEMO_MEDIA_THERMOSTAT_ENTITY = "climate.media_room"
DEMO_MEDIA_TEMPERATURE_SENSOR = "sensor.media_room_temperature"
DEMO_MEDIA_HUMIDITY_SENSOR = "sensor.media_room_humidity"
DEMO_POLL_INTERVAL_SECONDS = 0.4
DEMO_MAX_POLLS = 75
DEMO_THERMOSTAT_MIN_SETPOINT = 74.0
DEMO_THERMOSTAT_MAX_SETPOINT = 80.0


class DemoRequest(BaseModel):
    code: str


class ThermostatReasoning(BaseModel):
    summary: str
    recommended_temperature: float = Field(ge=60.0, le=90.0)
    rationale: str


class FeedbackSuggestion(BaseModel):
    category: str = Field(pattern="^(energy|security|occupancy|comfort)$")
    priority: str = Field(pattern="^(LOW|MEDIUM|HIGH)$")
    title: str
    detail: str


class PeriodicFeedback(BaseModel):
    summary: str
    suggestions: list[FeedbackSuggestion] = Field(default_factory=list)


@router.get("", response_class=HTMLResponse)
async def demo_page() -> HTMLResponse:
    """Serve the browser demo control surface."""
    page = Path(__file__).resolve().parents[1] / "static" / "demo.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.post("/demo1")
async def run_demo1(req: DemoRequest) -> StreamingResponse:
    """Run the live Demo1 sequence and stream human-readable progress."""
    if req.code.strip() != DEMO_ACCESS_CODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid demo code",
        )

    return StreamingResponse(
        _demo1_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/demo2")
async def run_demo2(req: DemoRequest) -> StreamingResponse:
    """Run the thermostat-focused Demo2 sequence."""
    if req.code.strip() != DEMO2_ACCESS_CODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid demo code",
        )

    return StreamingResponse(
        _demo2_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/feedback")
async def periodic_feedback() -> dict[str, Any]:
    """Return passive LLM-backed household feedback without taking actions."""
    return await collect_periodic_feedback(trigger="api")


async def collect_periodic_feedback(trigger: str = "manual") -> dict[str, Any]:
    """Collect suggestion-only household feedback for UI or background monitoring."""
    states = await _read_all_ha_states()
    snapshot = _build_household_feedback_snapshot(states)
    feedback = await _llm_periodic_feedback(snapshot)
    await write_audit_event_async(
        "feedback.generated",
        {
            "mode": "suggestions_only",
            "source": feedback["source"],
            "summary": feedback["summary"],
            "suggestions": feedback["suggestions"],
            "suggestion_count": len(feedback["suggestions"]),
            "occupancy_status": snapshot["occupancy_status"],
            "lights_on_count": len(snapshot["lights_on"]),
            "switches_on_count": len(snapshot["switches_on"]),
            "active_motion_count": len(snapshot["active_motion"]),
            "top_power_now_w": snapshot["top_power_now_w"][:3],
            "warning": feedback["warning"],
            "trigger": trigger,
        },
    )
    return {
        "mode": "suggestions_only",
        "source": feedback["source"],
        "warning": feedback["warning"],
        "summary": feedback["summary"],
        "suggestions": feedback["suggestions"],
        "snapshot": snapshot,
    }


@router.get("/audit")
async def recent_audit(limit: int = 50) -> dict[str, Any]:
    """Return recent autonomous runtime events from the durable audit log."""
    bounded_limit = max(1, min(limit, 200))
    return {
        "events": read_recent_audit_events(bounded_limit),
        "limit": bounded_limit,
    }


@router.get("/audit/summary")
async def audit_summary(limit: int = 500) -> dict[str, Any]:
    """Return aggregate health metrics from recent audit events."""
    bounded_limit = max(1, min(limit, 5000))
    events = read_recent_audit_events(bounded_limit)
    return {
        "limit": bounded_limit,
        "summary": summarize_audit_events(events),
    }


async def _demo1_events() -> AsyncIterator[str]:
    yield _event(
        "start",
        "Demo1 started",
        "Replaying smart-home events and letting EcoNest route them to agents.",
    )

    mysql_ok = await healthcheck_mysql()
    arcade_ok = await healthcheck_arcadedb()
    yield _event(
        "health",
        "Infrastructure check",
        "MySQL and ArcadeDB are reachable."
        if mysql_ok and arcade_ok
        else "One or more infrastructure services are degraded.",
        {"mysql": mysql_ok, "arcadedb": arcade_ok},
        ok=mysql_ok and arcade_ok,
    )

    initial_state = await _read_ha_state(DEMO_MEDIA_LIGHT_ENTITY)
    yield _event(
        "home_assistant",
        "Media room light before event",
        _state_summary(initial_state),
        {"entity_id": DEMO_MEDIA_LIGHT_ENTITY, "state": initial_state},
        ok=bool(initial_state.get("success")),
    )

    energy_task = Task(
        intent="energy anomaly detected in media room",
        payload={
            "type": "energy",
            "room": "media room",
            "device_name": "Media Room Plug",
            "current_power_w": 650,
            "baseline_w": 100,
            "trigger": "Demo1",
            "user_role": Role.HOMEOWNER.value,
        },
        timeout_seconds=30,
        metadata={
            "source": "demo_event_replay",
            "event_type": "energy_anomaly",
            "user_role": Role.HOMEOWNER.value,
        },
    )
    energy_task_id = await _demo_orchestrator.submit(energy_task)
    yield _event(
        "agent",
        "Energy anomaly submitted",
        "EcoNest is routing an unusual power spike to the energy agent.",
        {"task_id": energy_task_id, "intent": energy_task.intent},
    )

    energy_result = await _wait_for_task(energy_task_id)
    if energy_result is None:
        yield _event(
            "failed",
            "Energy agent task timed out",
            "The energy agent did not finish within the demo window.",
            {"task_id": energy_task_id},
            ok=False,
        )
        return

    yield _event(
        "agent_result",
        "Energy agent result",
        _agent_summary(energy_result),
        energy_result.model_dump(),
        ok=energy_result.success,
    )

    device_task = Task(
        intent="motion detected in media room, turn on media room light",
        payload={
            "event_type": "motion_detected",
            "room": "media room",
            "device_id": DEMO_MEDIA_LIGHT_ENTITY,
            "entity_id": DEMO_MEDIA_LIGHT_ENTITY,
            "domain": "light",
            "action": "turn_on",
            "expected_outcome": {
                "entity_id": DEMO_MEDIA_LIGHT_ENTITY,
                "state": "on",
            },
            "trigger": "Demo1",
            "user_role": Role.HOMEOWNER.value,
        },
        timeout_seconds=30,
        metadata={
            "source": "ha_event_replay",
            "event_type": "motion_detected",
            "user_role": Role.HOMEOWNER.value,
        },
    )
    device_task_id = await _demo_orchestrator.submit(device_task)
    yield _event(
        "agent",
        "Motion event submitted",
        "EcoNest is routing the motion event to the device agent for action.",
        {"task_id": device_task_id, "intent": device_task.intent},
    )

    device_result = await _wait_for_task(device_task_id)
    if device_result is None:
        yield _event(
            "failed",
            "Device agent task timed out",
            "The device agent did not finish within the demo window.",
            {"task_id": device_task_id},
            ok=False,
        )
        return

    yield _event(
        "agent_result",
        "Device agent result",
        _agent_summary(device_result),
        device_result.model_dump(),
        ok=device_result.success,
    )

    final_state = await _read_ha_state(DEMO_MEDIA_LIGHT_ENTITY)
    yield _event(
        "home_assistant",
        "Media room light after event",
        _state_summary(final_state),
        {"entity_id": DEMO_MEDIA_LIGHT_ENTITY, "state": final_state},
        ok=bool(final_state.get("success")),
    )

    yield _event(
        "occupancy",
        "Vacancy timeout detected",
        "Simulated follow-up event: 10 minutes pass with no media room motion.",
        {
            "room": "media room",
            "occupancy_signal": "no_motion_for_10_minutes",
            "policy": "vacant rooms should not keep nonessential lights on",
        },
    )

    reasoning = await _llm_light_policy_reasoning(final_state)
    yield _event(
        "llm_reasoning",
        "LLM policy reasoning",
        reasoning["summary"],
        reasoning,
        ok=reasoning["source"] == "ollama",
    )

    off_task_id: str | None = None
    off_result: Result | None = None
    off_state: dict[str, Any] | None = None
    if reasoning["recommended_action"] == "turn_off":
        off_task = Task(
            intent="room is vacant, turn off media room light",
            payload={
                "event_type": "occupancy_cleared",
                "room": "media room",
                "device_id": DEMO_MEDIA_LIGHT_ENTITY,
                "entity_id": DEMO_MEDIA_LIGHT_ENTITY,
                "domain": "light",
                "action": "turn_off",
                "expected_outcome": {
                    "entity_id": DEMO_MEDIA_LIGHT_ENTITY,
                    "state": "off",
                },
                "trigger": "Demo1",
                "user_role": Role.HOMEOWNER.value,
            },
            timeout_seconds=30,
            metadata={
                "source": "llm_policy_recommendation",
                "event_type": "occupancy_cleared",
                "user_role": Role.HOMEOWNER.value,
            },
        )
        off_task_id = await _demo_orchestrator.submit(off_task)
        yield _event(
            "agent",
            "Policy action submitted",
            "EcoNest is routing the LLM recommendation to the device agent.",
            {"task_id": off_task_id, "intent": off_task.intent},
        )

        off_result = await _wait_for_task(off_task_id)
        if off_result is None:
            yield _event(
                "failed",
                "Policy action timed out",
                "The device agent did not finish the turn-off action within the demo window.",
                {"task_id": off_task_id},
                ok=False,
            )
            return

        yield _event(
            "agent_result",
            "Policy action result",
            _agent_summary(off_result),
            off_result.model_dump(),
            ok=off_result.success,
        )

        off_state = await _read_ha_state(DEMO_MEDIA_LIGHT_ENTITY)
        yield _event(
            "home_assistant",
            "Media room light after policy action",
            _state_summary(off_state),
            {"entity_id": DEMO_MEDIA_LIGHT_ENTITY, "state": off_state},
            ok=bool(off_state.get("success")),
        )

    yield _event(
        "complete",
        "Demo1 complete",
        "The events were routed through EcoNest, LLM reasoning was shown, and Home Assistant state was checked.",
        {
            "energy_task_id": energy_task_id,
            "device_task_id": device_task_id,
            "policy_task_id": off_task_id,
            "agents": [
                agent
                for agent in (
                    energy_result.agent,
                    device_result.agent,
                    off_result.agent if off_result else None,
                )
                if agent is not None
            ],
            "llm_source": reasoning["source"],
            "recommended_action": reasoning["recommended_action"],
            "verified": (
                off_result.metadata.get("verified")
                if off_result
                else device_result.metadata.get("verified")
            ),
            "confidence": off_result.confidence if off_result else device_result.confidence,
            "final_state": _ha_state_value(off_state or final_state),
        },
        ok=energy_result.success
        and device_result.success
        and (off_result.success if off_result else True),
    )


async def _demo2_events() -> AsyncIterator[str]:
    yield _event(
        "start",
        "Demo2 started",
        "Reading the media room thermostat and asking Ollama to reason about comfort and energy.",
    )

    mysql_ok = await healthcheck_mysql()
    arcade_ok = await healthcheck_arcadedb()
    yield _event(
        "health",
        "Infrastructure check",
        "MySQL and ArcadeDB are reachable."
        if mysql_ok and arcade_ok
        else "One or more infrastructure services are degraded.",
        {"mysql": mysql_ok, "arcadedb": arcade_ok},
        ok=mysql_ok and arcade_ok,
    )

    thermostat_state = await _read_ha_state(DEMO_MEDIA_THERMOSTAT_ENTITY)
    temperature_state = await _read_ha_state(DEMO_MEDIA_TEMPERATURE_SENSOR)
    humidity_state = await _read_ha_state(DEMO_MEDIA_HUMIDITY_SENSOR)
    yield _event(
        "home_assistant",
        "Media room climate snapshot",
        _climate_snapshot_summary(
            thermostat_state,
            temperature_state,
            humidity_state,
        ),
        {
            "thermostat": thermostat_state,
            "temperature_sensor": temperature_state,
            "humidity_sensor": humidity_state,
        },
        ok=bool(thermostat_state.get("success")),
    )

    reasoning = await _llm_thermostat_reasoning(
        thermostat_state,
        temperature_state,
        humidity_state,
    )
    yield _event(
        "llm_reasoning",
        "Thermostat LLM reasoning",
        reasoning["summary"],
        reasoning,
        ok=reasoning["source"] in {"ollama", "policy_guarded_ollama"},
    )

    setpoint = reasoning["bounded_temperature"]
    climate_task = Task(
        intent="adjust media room thermostat based on comfort and energy reasoning",
        payload={
            "event_type": "comfort_energy_review",
            "room": "media room",
            "device_id": DEMO_MEDIA_THERMOSTAT_ENTITY,
            "entity_id": DEMO_MEDIA_THERMOSTAT_ENTITY,
            "domain": "climate",
            "action": "set_temperature",
            "temperature": setpoint,
            "expected_outcome": {
                "entity_id": DEMO_MEDIA_THERMOSTAT_ENTITY,
                "attribute": "temperature",
                "value": setpoint,
            },
            "trigger": "Demo2",
            "user_role": Role.HOMEOWNER.value,
        },
        timeout_seconds=30,
        metadata={
            "source": "llm_climate_recommendation",
            "event_type": "comfort_energy_review",
            "user_role": Role.HOMEOWNER.value,
        },
    )
    climate_task_id = await _demo_orchestrator.submit(climate_task)
    yield _event(
        "agent",
        "Thermostat action submitted",
        "EcoNest is routing the bounded LLM recommendation to the device agent.",
        {"task_id": climate_task_id, "intent": climate_task.intent},
    )

    climate_result = await _wait_for_task(climate_task_id)
    if climate_result is None:
        yield _event(
            "failed",
            "Thermostat action timed out",
            "The device agent did not finish the thermostat action within the demo window.",
            {"task_id": climate_task_id},
            ok=False,
        )
        return

    yield _event(
        "agent_result",
        "Thermostat action result",
        _agent_summary(climate_result),
        climate_result.model_dump(),
        ok=climate_result.success,
    )

    updated_thermostat_state = await _read_ha_state(DEMO_MEDIA_THERMOSTAT_ENTITY)
    yield _event(
        "home_assistant",
        "Media room thermostat after action",
        _thermostat_target_summary(updated_thermostat_state),
        {
            "entity_id": DEMO_MEDIA_THERMOSTAT_ENTITY,
            "state": updated_thermostat_state,
        },
        ok=bool(updated_thermostat_state.get("success")),
    )

    yield _event(
        "complete",
        "Demo2 complete",
        "Ollama reasoned over the climate context, EcoNest bounded the recommendation, and the device agent applied it through Home Assistant.",
        {
            "task_id": climate_task_id,
            "agents": ["llm", climate_result.agent],
            "llm_source": reasoning["source"],
            "recommended_temperature": reasoning["recommended_temperature"],
            "bounded_temperature": setpoint,
            "verified": climate_result.metadata.get("verified"),
            "confidence": climate_result.confidence,
            "final_target_temperature": _ha_attribute_value(
                updated_thermostat_state,
                "temperature",
            ),
        },
        ok=climate_result.success,
    )


async def _read_ha_state(entity_id: str) -> dict[str, Any]:
    result = await ha_get_state_handler(HAGetStateInput(entity_id=entity_id))
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


async def _read_all_ha_states() -> list[dict[str, Any]]:
    token = settings.HA_TOKEN or os.getenv("HA_TOKEN", "")
    if not token:
        return []
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{settings.HA_URL}/api/states",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


async def _wait_for_task(task_id: str) -> Result | None:
    for _ in range(DEMO_MAX_POLLS):
        result = await _demo_orchestrator.get_result(task_id)
        if result is not None:
            return result
        await asyncio.sleep(DEMO_POLL_INTERVAL_SECONDS)
    return None


def _build_household_feedback_snapshot(states: list[dict[str, Any]]) -> dict[str, Any]:
    people = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith(("person.", "device_tracker."))
    ]
    occupancy_status = _occupancy_status(people)
    transition = _record_occupancy_observation(occupancy_status)

    lights_on = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("light.")
        and str(item.get("state", "")).lower() == "on"
    ]
    switches_on = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("switch.")
        and str(item.get("state", "")).lower() == "on"
    ]
    covers_open = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("cover.")
        and str(item.get("state", "")).lower() not in {"closed", "closing"}
    ]
    active_motion = [
        _entity_summary(item)
        for item in states
        if str(item.get("entity_id", "")).startswith("binary_sensor.")
        and "motion" in _entity_text(item)
        and str(item.get("state", "")).lower() == "on"
    ]
    power_now = sorted(
        [
            _numeric_sensor_summary(item)
            for item in states
            if "power_minute_average" in str(item.get("entity_id", ""))
        ],
        key=lambda item: item["value"],
        reverse=True,
    )[:8]
    energy_today = sorted(
        [
            _numeric_sensor_summary(item)
            for item in states
            if "energy_today" in str(item.get("entity_id", ""))
        ],
        key=lambda item: item["value"],
        reverse=True,
    )[:8]

    return {
        "occupancy_status": occupancy_status,
        "occupancy_transition": transition,
        "observation_count": _feedback_memory["observation_count"],
        "recent_occupancy_transitions": list(_feedback_memory["transitions"][-5:]),
        "people": people[:10],
        "lights_on": lights_on[:15],
        "switches_on": switches_on[:15],
        "covers_open": covers_open,
        "active_motion": active_motion,
        "top_power_now_w": power_now,
        "top_energy_today_kwh": energy_today,
    }


async def _llm_periodic_feedback(snapshot: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        "You are EcoNest's passive home advisor. Review this Home Assistant snapshot "
        "and produce suggestion-only feedback. Do not tell the system to take actions. "
        "Focus on energy waste, security awareness, occupancy patterns, and comfort.\n\n"
        f"Snapshot JSON:\n{json.dumps(snapshot, default=str)}\n\n"
        "Return concise suggestions a resident could choose to do manually. "
        "Mention occupancy transitions if useful. Prefer JSON with summary and suggestions, "
        "but plain text bullets are acceptable. Only use entities and facts present "
        "in the snapshot. Do not invent examples, pets, passwords, biometric security, "
        "or generic cybersecurity advice."
    )
    client = LLMClient()
    try:
        raw = await client.generate(
            prompt,
            system="You produce passive smart-home suggestions. Never issue commands.",
            temperature=0.2,
        )
        result = _periodic_feedback_from_text(raw, snapshot)
        source = "ollama"
        warning = None
    except Exception as exc:
        result = _fallback_periodic_feedback(snapshot)
        source = "fallback"
        warning = str(exc)
    finally:
        await client.close()

    observed = _fallback_periodic_feedback(snapshot).suggestions
    combined = _merge_feedback_suggestions(observed, result.suggestions)
    suggestions = [item.model_dump() for item in combined[:6]]
    return {
        "source": source,
        "warning": warning,
        "summary": result.summary,
        "suggestions": suggestions,
    }


def _periodic_feedback_from_text(text: str, snapshot: dict[str, Any]) -> PeriodicFeedback:
    cleaned = _clean_jsonish_text(text)
    try:
        return PeriodicFeedback.model_validate_json(cleaned)
    except Exception:
        pass

    lines = [
        line.strip(" -*0123456789.").strip()
        for line in text.splitlines()
        if line.strip()
    ]
    suggestion_lines = [
        line
        for line in lines
        if len(line) > 20
        and not line.lower().startswith(("summary", "here"))
        and _is_grounded_feedback_line(line)
    ][:6]
    suggestions = [
        FeedbackSuggestion(
            category=_infer_feedback_category(line),
            priority=_infer_feedback_priority(line),
            title=_clean_feedback_line(line)[:70],
            detail=_clean_feedback_line(line),
        )
        for line in suggestion_lines
    ]
    if not suggestions:
        return _fallback_periodic_feedback(snapshot)
    return PeriodicFeedback(
        summary=(
            f"House appears {snapshot['occupancy_status']}; Ollama generated "
            "suggestion-only feedback from the latest Home Assistant snapshot."
        ),
        suggestions=suggestions,
    )


def _clean_jsonish_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _infer_feedback_category(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("door", "garage", "lock", "security", "motion")):
        return "security"
    if any(word in lowered for word in ("home", "away", "resident", "occupancy")):
        return "occupancy"
    if any(word in lowered for word in ("temperature", "humidity", "comfort")):
        return "comfort"
    return "energy"


def _is_grounded_feedback_line(text: str) -> bool:
    lowered = text.lower()
    banned = (
        "[example]",
        "password",
        "biometric",
        "pet",
        "animal",
        "sensitive information",
        "enable authentication",
    )
    return not any(term in lowered for term in banned)


def _clean_feedback_line(text: str) -> str:
    return (
        text.replace("**", "")
        .replace("[Example]:", "")
        .replace("Example:", "")
        .strip(" :-")
    )


def _infer_feedback_priority(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("urgent", "high", "open", "away")):
        return "HIGH"
    if any(word in lowered for word in ("review", "consider", "reduce", "highest")):
        return "MEDIUM"
    return "LOW"


def _fallback_periodic_feedback(snapshot: dict[str, Any]) -> PeriodicFeedback:
    suggestions: list[FeedbackSuggestion] = []
    if snapshot["occupancy_status"] == "away" and snapshot["lights_on"]:
        suggestions.append(
            FeedbackSuggestion(
                category="energy",
                priority="HIGH",
                title="Lights are on while the house appears away",
                detail=(
                    "Consider checking the listed lights when convenient, especially "
                    "if nobody is home."
                ),
            )
        )
    for top in snapshot["top_power_now_w"][:3]:
        suggestions.append(
            FeedbackSuggestion(
                category="energy",
                priority="MEDIUM",
                title=f"Review current load: {top['name']}",
                detail=(
                    f"{top['name']} is reading about {top['value']:.1f} W. "
                    "If this is unexpected, it may be worth reviewing."
                ),
            )
        )
    for light in snapshot["lights_on"][:3]:
        suggestions.append(
            FeedbackSuggestion(
                category="energy",
                priority="LOW",
                title=f"Light currently on: {light['name']}",
                detail=(
                    f"{light['name']} is on while the house appears "
                    f"{snapshot['occupancy_status']}. Consider turning it off if the "
                    "room is not being used."
                ),
            )
        )
    if snapshot["covers_open"]:
        suggestions.append(
            FeedbackSuggestion(
                category="security",
                priority="HIGH",
                title="A garage or cover is not closed",
                detail="Check open covers before leaving or going to sleep.",
            )
        )
    if snapshot["occupancy_transition"]:
        suggestions.append(
            FeedbackSuggestion(
                category="occupancy",
                priority="LOW",
                title="Occupancy pattern changed",
                detail=(
                    f"EcoNest observed a transition from "
                    f"{snapshot['occupancy_transition']['from']} to "
                    f"{snapshot['occupancy_transition']['to']}."
                ),
            )
        )
    if not suggestions:
        suggestions.append(
            FeedbackSuggestion(
                category="energy",
                priority="LOW",
                title="No urgent issues detected",
                detail="Current energy and security signals look normal.",
            )
        )
    return PeriodicFeedback(
        summary=(
            f"House appears {snapshot['occupancy_status']}; generated "
            "suggestion-only feedback from the latest Home Assistant snapshot."
        ),
        suggestions=suggestions,
    )


def _merge_feedback_suggestions(
    observed: list[FeedbackSuggestion],
    llm: list[FeedbackSuggestion],
) -> list[FeedbackSuggestion]:
    merged: list[FeedbackSuggestion] = []
    seen: set[str] = set()
    for suggestion in [*observed, *llm]:
        key = suggestion.title.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(suggestion)
    return merged


async def _llm_thermostat_reasoning(
    thermostat_state: dict[str, Any],
    temperature_state: dict[str, Any],
    humidity_state: dict[str, Any],
) -> dict[str, Any]:
    current_temperature = _float_or_none(_ha_state_value(temperature_state))
    current_humidity = _float_or_none(_ha_state_value(humidity_state))
    current_target = _float_or_none(_ha_attribute_value(thermostat_state, "temperature"))
    hvac_mode = _ha_state_value(thermostat_state)

    prompt = (
        "You are EcoNest's local smart-home climate reasoning model. "
        "Recommend one thermostat setpoint for a live demo.\n\n"
        "Context:\n"
        "- Room: media room\n"
        f"- Thermostat entity: {DEMO_MEDIA_THERMOSTAT_ENTITY}\n"
        f"- HVAC mode: {hvac_mode}\n"
        f"- Current room temperature: {current_temperature} F\n"
        f"- Current thermostat target: {current_target} F\n"
        f"- Current humidity: {current_humidity}%\n"
        "- Scenario: the room is occupied for a study/demo session, but EcoNest should avoid aggressive cooling.\n"
        f"- Demo safety range: {DEMO_THERMOSTAT_MIN_SETPOINT}-{DEMO_THERMOSTAT_MAX_SETPOINT} F.\n\n"
        "Return JSON only. Recommend a comfortable but energy-aware target temperature "
        "inside the demo safety range."
    )
    client = LLMClient()
    try:
        result = await client.generate_structured(
            [
                LLMMessage(
                    role="user",
                    content=prompt,
                )
            ],
            ThermostatReasoning,
            temperature=0.2,
        )
        source = "ollama"
        warning = None
    except Exception as exc:
        result = ThermostatReasoning(
            summary=(
                "The media room is warm, so EcoNest should choose a moderate "
                "cooling target instead of an aggressive setpoint."
            ),
            recommended_temperature=78.0,
            rationale="Fallback policy selected a conservative comfort setpoint.",
        )
        source = "fallback"
        warning = str(exc)
    finally:
        await client.close()

    bounded = _bounded_temperature(result.recommended_temperature)
    summary = result.summary.strip() or result.rationale.strip()
    if bounded != result.recommended_temperature:
        source = "policy_guarded_ollama" if source == "ollama" else source
        summary = (
            f"{summary} EcoNest bounded the setpoint to {bounded:g} F for the demo."
        )

    return {
        "agent": "llm",
        "source": source,
        "summary": summary,
        "rationale": result.rationale,
        "hvac_mode": hvac_mode,
        "current_temperature": current_temperature,
        "current_humidity": current_humidity,
        "current_target_temperature": current_target,
        "recommended_temperature": result.recommended_temperature,
        "bounded_temperature": bounded,
        "warning": warning,
    }


async def _llm_light_policy_reasoning(
    light_state: dict[str, Any],
) -> dict[str, Any]:
    state = _ha_state_value(light_state)
    recommended_action = "turn_off" if state == "on" else "none"
    prompt = (
        "You are EcoNest's local smart-home reasoning model. "
        "Explain this policy decision in exactly two short sentences for a live class demo.\n\n"
        "Context:\n"
        "- Room: media room\n"
        f"- Home Assistant light entity: {DEMO_MEDIA_LIGHT_ENTITY}\n"
        f"- Current light state: {state}\n"
        "- New event: 10 minutes have passed since the motion event\n"
        "- Current occupancy signal: no motion for 10 minutes, so the room is considered vacant\n"
        "- Energy policy: if a room is vacant and a nonessential light is on, "
        "recommend turning it off.\n\n"
        f"Required decision: {recommended_action}. "
        "If the required decision is turn_off, say the light should be off. "
        "Do not mention implementation details."
    )
    client = LLMClient()
    try:
        response = await client.generate(
            prompt,
            system="You produce concise smart-home reasoning for EcoNest demos.",
            temperature=0.2,
            max_retries=1,
        )
    except Exception as exc:
        response = (
            "The media room is vacant and the light is nonessential, so the light "
            "should be turned off to save energy."
        )
        source = "fallback"
        warning = str(exc)
    else:
        source = "ollama"
        warning = None
    finally:
        await client.close()

    raw_reasoning = response.strip()
    reasoning = _guard_light_policy_reasoning(
        raw_reasoning,
        state,
        recommended_action,
    )
    if reasoning != raw_reasoning and source == "ollama":
        source = "policy_guarded_ollama"
        warning = "Ollama response contradicted the required policy decision."

    return {
        "agent": "llm",
        "source": source,
        "model_reasoning": raw_reasoning,
        "summary": reasoning,
        "observed_state": state,
        "recommended_action": recommended_action,
        "warning": warning,
    }


def _guard_light_policy_reasoning(
    reasoning: str,
    state: str,
    recommended_action: str,
) -> str:
    fallback = (
        "The media room is now considered vacant, and the nonessential light is "
        f"currently {state}. EcoNest should turn it off to avoid wasting energy."
    )
    if not reasoning:
        return fallback

    normalized = reasoning.lower()
    says_keep_on = any(
        phrase in normalized
        for phrase in (
            "remain on",
            "stay on",
            "keep on",
            "keep the light on",
            "should be on",
            "should remain on",
        )
    )
    says_turn_off = any(
        phrase in normalized
        for phrase in (
            "turn it off",
            "turn off",
            "should be off",
            "switch it off",
            "shut off",
        )
    )

    if recommended_action == "turn_off" and (says_keep_on or not says_turn_off):
        return fallback
    return reasoning


def _event(
    event_type: str,
    title: str,
    message: str,
    details: dict[str, Any] | None = None,
    ok: bool = True,
) -> str:
    payload = {
        "type": event_type,
        "title": title,
        "message": message,
        "ok": ok,
        "details": details or {},
    }
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _state_summary(state_result: dict[str, Any]) -> str:
    if not state_result.get("success"):
        warnings = state_result.get("warnings") or ["Home Assistant state unavailable"]
        return "; ".join(str(warning) for warning in warnings)

    result = state_result.get("result")
    if not isinstance(result, dict):
        return "Home Assistant returned a state response."

    entity_id = result.get("entity_id", DEMO_MEDIA_LIGHT_ENTITY)
    state = result.get("state", "unknown")
    return f"{entity_id} is currently {state}."


def _climate_snapshot_summary(
    thermostat_state: dict[str, Any],
    temperature_state: dict[str, Any],
    humidity_state: dict[str, Any],
) -> str:
    hvac_mode = _ha_state_value(thermostat_state)
    current_temperature = _ha_state_value(temperature_state)
    humidity = _ha_state_value(humidity_state)
    target = _ha_attribute_value(thermostat_state, "temperature")
    return (
        f"Media room is {current_temperature} F with {humidity}% humidity; "
        f"thermostat is {hvac_mode} with target {target} F."
    )


def _thermostat_target_summary(state_result: dict[str, Any]) -> str:
    target = _ha_attribute_value(state_result, "temperature")
    hvac_mode = _ha_state_value(state_result)
    return f"{DEMO_MEDIA_THERMOSTAT_ENTITY} is {hvac_mode} with target {target} F."


def _ha_state_value(state_result: dict[str, Any] | None) -> str:
    if not state_result:
        return "unknown"
    result = state_result.get("result")
    if not isinstance(result, dict):
        return "unknown"
    return str(result.get("state", "unknown"))


def _ha_attribute_value(state_result: dict[str, Any] | None, key: str) -> Any:
    if not state_result:
        return None
    result = state_result.get("result")
    if not isinstance(result, dict):
        return None
    attributes = result.get("attributes")
    if not isinstance(attributes, dict):
        return None
    return attributes.get(key)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_temperature(value: float) -> float:
    bounded = max(
        DEMO_THERMOSTAT_MIN_SETPOINT,
        min(DEMO_THERMOSTAT_MAX_SETPOINT, float(value)),
    )
    return round(bounded, 1)


def _agent_summary(result: Result) -> str:
    if not result.success:
        return result.message or "The selected agent failed to complete the task."

    verified = result.metadata.get("verified")
    source = result.metadata.get("execution_source", "agent")
    return (
        f"{result.agent or 'Agent'} completed the task through {source}; "
        f"verified={verified}, confidence={result.confidence}."
    )


def _entity_summary(item: dict[str, Any]) -> dict[str, Any]:
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return {
        "entity_id": item.get("entity_id"),
        "name": attributes.get("friendly_name") or item.get("entity_id"),
        "state": item.get("state"),
    }


def _numeric_sensor_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = _entity_summary(item)
    summary["value"] = _float_or_none(item.get("state")) or 0.0
    return summary


def _entity_text(item: dict[str, Any]) -> str:
    attributes = item.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return f"{item.get('entity_id', '')} {attributes.get('friendly_name', '')}".lower()


def _occupancy_status(people: list[dict[str, Any]]) -> str:
    if not people:
        return "unknown"
    if any(str(person.get("state", "")).lower() == "home" for person in people):
        return "home"
    if all(
        str(person.get("state", "")).lower() in {"not_home", "away"}
        for person in people
    ):
        return "away"
    return "mixed"


def _record_occupancy_observation(status: str) -> dict[str, Any] | None:
    previous = _feedback_memory.get("last_occupancy")
    _feedback_memory["observation_count"] = int(_feedback_memory["observation_count"]) + 1
    _feedback_memory["last_occupancy"] = status
    if previous is None or previous == status:
        return None
    transition = {
        "from": previous,
        "to": status,
        "observation": _feedback_memory["observation_count"],
    }
    _feedback_memory["transitions"].append(transition)
    return transition
