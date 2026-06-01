"""Base agent abstract class and shared execution models."""

import json
import logging
from abc import ABC, abstractmethod
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.core.permissions import AGENT_RUN
from orchestrator.llm.client import LLMClient

logger = logging.getLogger(__name__)

Tool = str
Memory = dict[str, Any]


class Task(BaseModel):
    """A task to be processed by an agent."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    intent: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    user_id: str = ""
    timeout_seconds: int = Field(default=30, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    """Result of agent task execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any = None
    message: str = ""
    agent: str | None = None
    task_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )


class Status(BaseModel):
    """Health status of an agent."""

    model_config = ConfigDict(extra="forbid")

    healthy: bool
    message: str = ""
    agent: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base class for all EcoNest agents."""

    name: str = "base"
    tools: list[Tool] = []
    permissions: list[str] = [AGENT_RUN]

    def __init__(
        self,
        llm: LLMClient | None = None,
        memory: Memory | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        self.memory: Memory = memory or {}

    async def execute(self, task: Task) -> Result:
        """Run a task with shared capability checks and structured logging."""
        started_at = monotonic()
        result: Result
        try:
            routed_agent = task.metadata.get("routed_agent")
            if routed_agent != self.name and not await self.can_handle(task):
                result = Result(
                    success=False,
                    data={},
                    message=f"{self.name} cannot handle this task",
                    agent=self.name,
                    task_id=task.id,
                    error="unsupported_task",
                )
            else:
                result = await self.run(task)
                result = result.model_copy(
                    update={
                        "agent": result.agent or self.name,
                        "task_id": result.task_id or task.id,
                    }
                )
        except Exception as exc:
            result = Result(
                success=False,
                data={},
                message=f"{self.name} failed while running task",
                agent=self.name,
                task_id=task.id,
                error=exc.__class__.__name__,
                metadata={"error_message": str(exc)},
            )
        duration_ms = round((monotonic() - started_at) * 1000, 3)
        self._log_run(task, result, duration_ms)
        return result

    @abstractmethod
    async def run(self, task: Task) -> Result:
        """Execute the task and return a result."""
        raise NotImplementedError

    @abstractmethod
    async def can_handle(self, task: Task) -> bool:
        """Return True if this agent can handle the given task."""
        raise NotImplementedError

    async def healthcheck(self) -> Status:
        """Return health status. Subclasses may override."""
        return Status(
            healthy=True,
            message=f"{self.name} is healthy",
            agent=self.name,
            details={
                "tools": list(self.tools),
                "permissions": list(self.permissions),
            },
        )

    def _log_run(self, task: Task, result: Result, duration_ms: float) -> None:
        event = {
            "event": "agent.run",
            "agent": self.name,
            "task_id": task.id,
            "intent": task.intent,
            "user_id": task.user_id,
            "success": result.success,
            "message": result.message,
            "error": result.error,
            "duration_ms": duration_ms,
            "tools": list(self.tools),
            "permissions": list(self.permissions),
        }
        log_method = logger.info if result.success else logger.warning
        log_method(json.dumps(event, sort_keys=True))
