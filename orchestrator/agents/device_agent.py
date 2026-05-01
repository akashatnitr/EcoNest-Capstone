"""Device control agent."""

from orchestrator.agents.base import BaseAgent, Result, Task


class DeviceAgent(BaseAgent):
    """Responsibilities: device control (on/off/dim), capability enforcement,
    user request fulfillment.
    """

    name = "device"
    tools = ["ha_call_service", "query_arcadedb", "query_mysql"]
    permissions = ["device:read", "device:write", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = [
            "device",
            "turn on",
            "turn off",
            "dim",
            "light",
            "switch",
            "brightness",
        ]
        return any(kw in task.intent.lower() for kw in keywords)

    async def run(self, task: Task) -> Result:
        # Placeholder: real implementation would validate capability and call HA
        return Result(
            success=True,
            data={"action": "noop", "state": "unchanged"},
            message="Device action processed",
        )
