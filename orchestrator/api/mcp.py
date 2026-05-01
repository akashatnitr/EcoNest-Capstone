"""MCP orchestrator API routes."""

from typing import Annotated

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
):
    """Submit a task to the orchestrator."""
    if not has_permission(current_user.role, AGENT_RUN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent:run permission required",
        )
    from orchestrator.agents.base import Task

    task = Task(
        id="",
        intent=req.intent,
        payload=req.payload,
        user_id=str(current_user.id),
        timeout_seconds=req.timeout_seconds,
    )
    task_id = await _orchestrator.submit(task)
    return TaskResponse(task_id=task_id, status="submitted")


@router.get("/task/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """Get task status and result."""
    result = await _orchestrator.get_result(task_id)
    if result is None:
        return {"task_id": task_id, "status": "running", "result": None}
    return {
        "task_id": task_id,
        "status": "completed" if result.success else "failed",
        "result": result.data,
        "message": result.message,
    }


@router.get("/agents")
async def list_agents(
    current_user: Annotated[UserProfile, Depends(get_current_user)],
):
    """List registered agents and their health."""
    health = await _orchestrator.healthcheck()
    return {"agents": health}
