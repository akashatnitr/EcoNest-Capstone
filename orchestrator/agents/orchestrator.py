"""Task orchestrator that routes tasks to the appropriate agent."""

import asyncio
import uuid
from typing import Any, Dict

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.agents.device_agent import DeviceAgent
from orchestrator.agents.energy_agent import EnergyAgent
from orchestrator.agents.security_agent import SecurityAgent
from orchestrator.agents.sensor_agent import SensorAgent


class AgentOrchestrator:
    """Routes incoming tasks to the correct agent and manages lifecycle."""

    def __init__(self):
        self.agents: list[BaseAgent] = [
            EnergyAgent(),
            SecurityAgent(),
            SensorAgent(),
            DeviceAgent(),
        ]
        self._tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Result] = {}

    async def submit(self, task: Task) -> str:
        """Submit a task and return a task ID."""
        task.id = task.id or str(uuid.uuid4())
        asyncio.create_task(self._run_with_lifecycle(task))
        return task.id

    async def _run_with_lifecycle(self, task: Task) -> None:
        """Run task with timeout, retry, and result storage."""
        agent = await self._classify_and_route(task)
        if agent is None:
            self._results[task.id] = Result(
                success=False,
                data={},
                message="No agent could handle this task",
            )
            return

        for attempt in range(3):
            try:
                result = await asyncio.wait_for(
                    agent.run(task),
                    timeout=task.timeout_seconds,
                )
                self._results[task.id] = result
                return
            except asyncio.TimeoutError:
                if attempt == 2:
                    self._results[task.id] = Result(
                        success=False,
                        data={},
                        message=f"Task timed out after {task.timeout_seconds}s",
                    )
                    return
            except Exception as exc:
                if attempt == 2:
                    self._results[task.id] = Result(
                        success=False,
                        data={},
                        message=f"Task failed: {exc}",
                    )
                    return

    async def _classify_and_route(self, task: Task) -> BaseAgent | None:
        """Classify intent and route to the best agent."""
        # Rule-based routing first
        intent_map = {
            "energy": ["energy", "power", "efficiency", "pricing", "schedule"],
            "security": ["security", "motion", "intrusion", "alert", "garage"],
            "sensor": ["sensor", "health", "calibration", "offline"],
            "device": ["device", "turn on", "turn off", "dim", "light", "switch"],
        }
        intent_lower = task.intent.lower()
        for category, keywords in intent_map.items():
            if any(kw in intent_lower for kw in keywords):
                for agent in self.agents:
                    if agent.name == category:
                        if await agent.can_handle(task):
                            return agent
                        break

        # Fallback: ask each agent if it can handle
        for agent in self.agents:
            if await agent.can_handle(task):
                return agent
        return None

    async def get_result(self, task_id: str) -> Result | None:
        """Get the result for a task (None if still running)."""
        return self._results.get(task_id)

    async def healthcheck(self) -> dict[str, Any]:
        """Healthcheck all registered agents."""
        return {
            agent.name: (await agent.healthcheck()).__dict__ for agent in self.agents
        }
