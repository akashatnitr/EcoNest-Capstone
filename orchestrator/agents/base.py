"""Base agent abstract class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List
import json
import logging

from orchestrator.llm.client import LLMClient
from orchestrator.llm import memory
from pydantic import BaseModel, Field


class Task(BaseModel):
    id: str
    intent: str
    payload: dict[str, Any]
    user_id: str = ""
    timeout_seconds: int = 30
    priority: int = 0
    thread_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    success: bool
    data: Any
    message: str = ""
    confidence: float = 1.0


class Status(BaseModel):
    healthy: bool
    message: str = ""


class BaseAgent(ABC):
    """Abstract base class for all EcoNest agents."""

    name: str = "base"
    tools: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()
        self.memory = memory
        self.logger = logging.getLogger(f"agent.{self.name}")

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
        return Status(healthy=True, message=f"{self.name} is healthy")

    def log_event(
        self,
        event: str,
        **kwargs: Any,
    ) -> None:
        payload = {
            "agent": self.name,
            "event": event,
            **kwargs,
        }

        self.logger.info(json.dumps(payload))
