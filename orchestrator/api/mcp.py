"""MCP orchestrator API routes."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import AGENT_RUN, has_permission
from orchestrator.core.audit import read_recent_audit_events_async
from orchestrator.core.audit import read_recent_mcp_events_async

router = APIRouter(prefix="/mcp", tags=["mcp"])
_orchestrator = AgentOrchestrator()


class SubmitTaskRequest(BaseModel):
    intent: str
    payload: dict
    user_id: str = ""
    timeout_seconds: int = 30


class TaskResponse(BaseModel):
    task_id: str
    status: str


@router.get("/activity", response_class=HTMLResponse)
async def mcp_activity_page() -> HTMLResponse:
    """Serve the human-readable MCP tool and resource activity page."""
    page = Path(__file__).resolve().parents[1] / "static" / "mcp_activity.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/activity/events")
async def mcp_activity_events(limit: int = 100) -> dict[str, Any]:
    """Return recent tool and resource traces without exposing raw payloads."""
    bounded_limit = max(1, min(limit, 200))
    events = await read_recent_mcp_events_async(bounded_limit)
    traces = [_mcp_trace_view(event) for event in events]
    return {"events": list(reversed(traces)), "limit": bounded_limit}

@router.post("/task", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_task(
    req: SubmitTaskRequest,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> TaskResponse:
    """Submit a task to the orchestrator."""
    if not has_permission(current_user.role, AGENT_RUN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent:run permission required",
        )
    task_id = await _orchestrator.submit_http_api(
        intent=req.intent,
        payload=req.payload,
        user_id=str(current_user.id),
        user_role=current_user.role,
        timeout_seconds=req.timeout_seconds,
    )
    return TaskResponse(task_id=task_id, status="submitted")


@router.get("/task/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get task status and result."""
    result = await _orchestrator.get_result(task_id)
    if result is None:
        return {"task_id": task_id, "status": "running", "result": None}
    return {
        "task_id": task_id,
        "status": ("completed" if result.success else "failed"),
        "result": result.data,
        "message": result.message,
        "agent": result.agent,
        "confidence": result.confidence,
        "metadata": result.metadata,
    }


@router.get("/stats")
async def get_stats(
    current_user: Annotated[
        UserProfile,
        Depends(get_current_user),
    ],
) -> dict[str, Any]:
    return _orchestrator.stats()


@router.get("/agents")
async def list_agents(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
) -> dict[str, Any]:
    """List registered agents and their health."""
    health = await _orchestrator.healthcheck()
    return {
        "agents": health,
        "registered": [
            {
                "name": agent.name,
                "tools": getattr(agent, "tools", []),
                "permissions": getattr(
                    agent,
                    "permissions",
                    [],
                ),
            }
            for agent in _orchestrator.agents
        ],
    }


def _mcp_trace_view(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize durable trace records into a UI-safe activity item."""
    event_type = str(event.get("event_type", ""))
    is_tool = event_type == "mcp.tool.executed"
    return {
        "timestamp": event.get("timestamp"),
        "task_id": event.get("task_id") or "",
        "agent": event.get("agent") or "System",
        "source": event.get("source") or "internal",
        "kind": "tool" if is_tool else "resource",
        "name": event.get("tool") if is_tool else event.get("resource"),
        "success": event.get("success") is True,
        "duration_ms": event.get("duration_ms"),
        "arguments": event.get("arguments") if is_tool else {},
        "warnings": event.get("warnings") if is_tool else [],
        "error": event.get("error"),
    }
