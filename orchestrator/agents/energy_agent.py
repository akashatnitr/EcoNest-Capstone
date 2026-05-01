"""Energy optimization agent."""

from orchestrator.agents.base import BaseAgent, Result, Task


class EnergyAgent(BaseAgent):
    """Responsibilities: power optimization, TOU pricing awareness,
    schedule violations, anomaly detection.
    """

    name = "energy"
    tools = ["query_mysql", "query_arcadedb", "ha_get_state", "ha_turn_off"]
    permissions = ["device:read", "device:write", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = ["energy", "power", "efficiency", "pricing", "schedule", "cheap"]
        return any(kw in task.intent.lower() for kw in keywords)

    async def run(self, task: Task) -> Result:
        # Placeholder: real implementation would query DB, HA, and use LLM
        return Result(
            success=True,
            data={
                "recommendation": "Run high-load appliances after 9pm for off-peak pricing"
            },
            message="Energy review complete",
        )
