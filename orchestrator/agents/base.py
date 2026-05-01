"""Base agent abstract class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List

from orchestrator.llm.client import LLMClient


@dataclass
class Task:
    """A task to be processed by an agent."""

    id: str
    intent: str
    payload: dict
    user_id: str = ""
    timeout_seconds: int = 30


@dataclass
class Result:
    """Result of agent task execution."""

    success: bool
    data: Any
    message: str = ""


@dataclass
class Status:
    """Health status of an agent."""

    healthy: bool
    message: str = ""


class BaseAgent(ABC):
    """Abstract base class for all EcoNest agents."""

    name: str = "base"
    tools: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()
        self.memory: dict[str, Any] = {}

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
