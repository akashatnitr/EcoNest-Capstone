"""Professor-facing live demo routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from orchestrator.agents.base import Result, Task
from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.core.database import healthcheck_arcadedb, healthcheck_mysql
from orchestrator.core.permissions import Role
from orchestrator.mcp.tools.ha_tools import HAGetStateInput, ha_get_state_handler

router = APIRouter(prefix="/demo", tags=["demo"])
_demo_orchestrator = AgentOrchestrator()

DEMO_ACCESS_CODE = "Demo1"
DEMO_MEDIA_LIGHT_ENTITY = "light.upstairs_media_light_1"
DEMO_POLL_INTERVAL_SECONDS = 0.4
DEMO_MAX_POLLS = 75


class DemoRequest(BaseModel):
    code: str


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
        "complete",
        "Demo1 complete",
        "The events were routed through EcoNest and the Home Assistant state was checked.",
        {
            "energy_task_id": energy_task_id,
            "device_task_id": device_task_id,
            "agents": [
                agent
                for agent in (energy_result.agent, device_result.agent)
                if agent is not None
            ],
            "verified": device_result.metadata.get("verified"),
            "confidence": device_result.confidence,
        },
        ok=energy_result.success and device_result.success,
    )


async def _read_ha_state(entity_id: str) -> dict[str, Any]:
    result = await ha_get_state_handler(HAGetStateInput(entity_id=entity_id))
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


async def _wait_for_task(task_id: str) -> Result | None:
    for _ in range(DEMO_MAX_POLLS):
        result = await _demo_orchestrator.get_result(task_id)
        if result is not None:
            return result
        await asyncio.sleep(DEMO_POLL_INTERVAL_SECONDS)
    return None


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


def _agent_summary(result: Result) -> str:
    if not result.success:
        return result.message or "The selected agent failed to complete the task."

    verified = result.metadata.get("verified")
    source = result.metadata.get("execution_source", "agent")
    return (
        f"{result.agent or 'Agent'} completed the task through {source}; "
        f"verified={verified}, confidence={result.confidence}."
    )
