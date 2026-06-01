"""MCP orchestrator API routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from orchestrator.agents.orchestrator import AgentOrchestrator
from orchestrator.api.auth import UserProfile, get_current_user
from orchestrator.core.permissions import AGENT_RUN, has_permission

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
