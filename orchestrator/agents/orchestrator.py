"""Task orchestrator that routes tasks to the appropriate agent."""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.agents.device_agent import DeviceAgent
from orchestrator.agents.energy_agent import EnergyAgent
from orchestrator.agents.security_agent import SecurityAgent
from orchestrator.agents.sensor_agent import SensorAgent
from orchestrator.core.database import arcadedb_query
from orchestrator.core.permissions import Role, normalize_role
from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage

MAX_RETRIES = 3
NIGHT_CONTROL_START_HOUR = 23
NIGHT_CONTROL_END_HOUR = 6


class IntentClassification(BaseModel):
    """LLM fallback output for intent routing."""

    category: str = Field(pattern="^(energy|security|sensor|device|multi|unknown)$")


INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "energy": ("energy", "power", "efficiency", "pricing", "schedule"),
    "security": ("security", "motion", "intrusion", "alert", "garage"),
    "sensor": ("sensor", "health", "calibration", "offline"),
    "device": ("device", "turn on", "turn off", "dim", "light", "switch"),
}

DEVICE_CONTROL_KEYWORDS = (
    "device",
    "turn on",
    "turn off",
    "dim",
    "light",
    "switch",
    "brightness",
    "open",
    "close",
)
SMS_KEYWORDS = ("sms", "text message", "send text", "notify phone")


class AgentOrchestrator:
    """Routes incoming tasks to the correct agent and manages lifecycle."""

    def __init__(
        self,
        agents: Sequence[BaseAgent] | None = None,
        llm: LLMClient | None = None,
        current_hour_provider: Callable[[], int] | None = None,
    ) -> None:
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
        }
        self.agents = list(
            agents
            or [
                EnergyAgent(),
                SecurityAgent(),
                SensorAgent(),
                DeviceAgent(),
            ]
        )
        self.llm = llm or LLMClient()
        self.current_hour_provider = current_hour_provider or (
            lambda: datetime.now().hour
        )
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._results: dict[str, Result] = {}

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def submit_http_api(
        self,
        intent: str,
        payload: dict[str, Any] | None = None,
        user_id: str = "",
        user_role: str = "",
        timeout_seconds: int = 30,
    ) -> str:
        """Submit a task received through the authenticated HTTP API."""
        return await self.submit(
            Task(
                intent=intent,
                payload=payload or {},
                user_id=user_id,
                timeout_seconds=timeout_seconds,
                metadata={"source": "http_api", "user_role": user_role},
            )
        )

    async def submit_scheduled(
        self,
        intent: str,
        payload: dict[str, Any] | None = None,
        schedule_id: str = "",
        timeout_seconds: int = 30,
    ) -> str:
        """Submit a task created by a scheduled job."""
        return await self.submit(
            Task(
                intent=intent,
                payload=payload or {},
                timeout_seconds=timeout_seconds,
                metadata={"source": "scheduled_cron", "schedule_id": schedule_id},
            )
        )

    async def submit_home_assistant_webhook(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: int = 30,
    ) -> str:
        """Submit a task created by a Home Assistant webhook/event."""
        event_payload = payload or {}
        intent = str(event_payload.get("intent") or event_type)
        return await self.submit(
            Task(
                intent=intent,
                payload=event_payload,
                timeout_seconds=timeout_seconds,
                metadata={"source": "ha_webhook", "event_type": event_type},
            )
        )

    async def submit(self, task: Task) -> str:
        """Submit a task and return a task ID."""
        task = task.model_copy(update={"id": task.id or str(uuid.uuid4())})
        running_task = asyncio.create_task(self._run_with_lifecycle(task))
        self._tasks[task.id] = running_task
        self._stats["submitted"] += 1
        running_task.add_done_callback(lambda _: self._tasks.pop(task.id, None))
        return task.id

    async def _run_with_lifecycle(self, task: Task) -> None:
        """Run task with timeout, retry, and result storage."""
        policy_result = await self._enforce_global_policy(task)
        if policy_result is not None:
            self._results[task.id] = policy_result
            if self._is_device_control_task(task):
                await self._log_device_control_to_graph(task, policy_result)
            return

        agents = await self._select_agents(task)
        if not agents:
            self._results[task.id] = Result(
                success=False,
                data={},
                message="No agent could handle this task",
                task_id=task.id,
                error="no_agent",
            )
            return

        results = await asyncio.gather(
            *(self._run_agent_with_retries(agent, task) for agent in agents)
        )
        result = self._aggregate_results(task, results)
        self._results[task.id] = result
        if result.success:
            self._stats["completed"] += 1
        else:
            self._stats["failed"] += 1

        if self._is_device_control_task(task):
            await self._log_device_control_to_graph(task, result)

    async def _run_agent_with_retries(self, agent: BaseAgent, task: Task) -> Result:
        routed_task = task.model_copy(
            update={"metadata": {**task.metadata, "routed_agent": agent.name}}
        )
        for attempt in range(MAX_RETRIES):
            try:
                result = await asyncio.wait_for(
                    agent.execute(routed_task),
                    timeout=routed_task.timeout_seconds,
                )
                if (
                    result.success
                    or result.error == "unsupported_task"
                    or attempt == MAX_RETRIES - 1
                ):
                    return result
            except asyncio.TimeoutError:
                if attempt == MAX_RETRIES - 1:
                    return Result(
                        success=False,
                        data={},
                        message=f"Task timed out after {routed_task.timeout_seconds}s",
                        agent=agent.name,
                        task_id=routed_task.id,
                        error="timeout",
                    )
            except Exception as exc:
                if attempt == MAX_RETRIES - 1:
                    return Result(
                        success=False,
                        data={},
                        message=f"Task failed: {exc}",
                        agent=agent.name,
                        task_id=routed_task.id,
                        error=exc.__class__.__name__,
                    )
        return Result(
            success=False,
            data={},
            message="Task failed after retries",
            agent=agent.name,
            task_id=routed_task.id,
            error="retry_exhausted",
        )

    async def _classify_and_route(self, task: Task) -> BaseAgent | None:
        """Classify intent and route to the best agent."""
        agents = await self._select_agents(task, aggregate=False)
        return agents[0] if agents else None

    async def _select_agents(
        self,
        task: Task,
        aggregate: bool = True,
    ) -> list[BaseAgent]:
        """Classify intent and return one or more capable agents."""
        if self._should_aggregate(task) and aggregate:
            capable_agents = [
                agent for agent in self.agents if await agent.can_handle(task)
            ]
            return capable_agents or list(self.agents)

        category = await self._classify_intent(task)
        if category != "unknown":
            agent = await self._agent_for_category(task, category)
            if agent is not None:
                return [agent]

        for agent in self.agents:
            if await agent.can_handle(task):
                return [agent]
        return []

    async def _classify_intent(self, task: Task) -> str:
        """Classify intent with rules first and LLM fallback second."""
        intent_lower = task.intent.lower()
        for category, keywords in INTENT_KEYWORDS.items():
            if any(kw in intent_lower for kw in keywords):
                return category

        try:
            classification = await self.llm.generate_structured(
                [
                    LLMMessage(
                        role="user",
                        content=(
                            "Classify this smart-home task into one category: "
                            "energy, security, sensor, device, multi, or unknown.\n"
                            f"Intent: {task.intent}\nPayload: {task.payload}"
                        ),
                    )
                ],
                IntentClassification,
                temperature=0.0,
            )
            return classification.category
        except Exception:
            return "unknown"

    async def _agent_for_category(
        self,
        _task: Task,
        category: str,
    ) -> BaseAgent | None:
        for agent in self.agents:
            if agent.name == category:
                return agent
        return None

    async def get_result(self, task_id: str) -> Result | None:
        """Get the result for a task (None if still running)."""
        return self._results.get(task_id)

    async def healthcheck(self) -> dict[str, Any]:
        """Healthcheck all registered agents."""
        return {
            agent.name: (await agent.healthcheck()).model_dump()
            for agent in self.agents
        }

    def _should_aggregate(self, task: Task) -> bool:
        route = task.metadata.get("route")
        if route in {"all", "multi"}:
            return True
        aggregate = task.metadata.get("aggregate")
        if isinstance(aggregate, bool):
            return aggregate
        intent_lower = task.intent.lower()
        return any(word in intent_lower for word in ("overview", "summary", "status"))

    def _aggregate_results(self, task: Task, results: Sequence[Result]) -> Result:
        if len(results) == 1:
            return results[0]

        successful = [result for result in results if result.success]
        return Result(
            success=bool(successful) and len(successful) == len(results),
            data={
                "results": [
                    {
                        "agent": result.agent,
                        "success": result.success,
                        "data": result.data,
                        "message": result.message,
                        "error": result.error,
                    }
                    for result in results
                ]
            },
            message=f"Aggregated {len(results)} agent results",
            task_id=task.id,
            metadata={
                "agents": [result.agent for result in results if result.agent],
                "successful_agents": [
                    result.agent for result in successful if result.agent
                ],
            },
        )

    async def _enforce_global_policy(self, task: Task) -> Result | None:
        role = self._task_role(task)
        if self._is_device_control_task(task) and self._is_restricted_control_hour():
            if role not in {Role.HOMEOWNER, Role.SUPERADMIN}:
                return Result(
                    success=False,
                    data={},
                    message="Device control is restricted between 11pm and 6am",
                    task_id=task.id,
                    error="policy_device_control_quiet_hours",
                    metadata={"role": role.value if role else None},
                )

        if self._is_sms_task(task) and role in {Role.GUEST, Role.FAMILY_MEMBER}:
            return Result(
                success=False,
                data={},
                message="SMS actions are not allowed for guest or family_member roles",
                task_id=task.id,
                error="policy_sms_role_restricted",
                metadata={"role": role.value if role else None},
            )
        return None

    def _task_role(self, task: Task) -> Role | None:
        role = task.metadata.get("user_role") or task.payload.get("user_role")
        return normalize_role(str(role)) if role else None

    def _is_restricted_control_hour(self) -> bool:
        hour = self.current_hour_provider()
        return hour >= NIGHT_CONTROL_START_HOUR or hour < NIGHT_CONTROL_END_HOUR

    def _is_device_control_task(self, task: Task) -> bool:
        action = str(task.payload.get("action", "")).lower()
        domain = str(task.payload.get("domain", "")).lower()
        intent = task.intent.lower()
        return (
            domain in {"device", "light", "switch", "cover", "climate", "fan"}
            or action
            in {
                "turn_on",
                "turn_off",
                "toggle",
                "set_brightness",
                "open",
                "close",
                "set_temperature",
            }
            or any(keyword in intent for keyword in DEVICE_CONTROL_KEYWORDS)
        )

    def _is_sms_task(self, task: Task) -> bool:
        channel = str(task.payload.get("channel", "")).lower()
        action = str(task.payload.get("action", "")).lower()
        intent = task.intent.lower()
        return (
            channel == "sms"
            or action in {"send_sms", "sms"}
            or any(keyword in intent for keyword in SMS_KEYWORDS)
        )

    async def _log_device_control_to_graph(self, task: Task, result: Result) -> None:
        command = (
            "CREATE VERTEX Action "
            f"SET name = {_sql_string(task.payload.get('action') or task.intent)}, "
            f"task_id = {_sql_string(task.id)}, "
            f"user_id = {_sql_string(task.user_id)}, "
            f"success = {str(result.success).lower()}, "
            "timestamp = datetime()"
        )
        try:
            await arcadedb_query("sql", command, readonly=False)
        except Exception:
            return


def _sql_string(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"
