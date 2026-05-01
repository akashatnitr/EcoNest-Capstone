"""Sensor health monitoring agent."""

from orchestrator.agents.base import BaseAgent, Result, Task


class SensorAgent(BaseAgent):
    """Responsibilities: sensor health monitoring, data quality,
    calibration drift detection.
    """

    name = "sensor"
    tools = ["query_mysql", "query_arcadedb", "ha_get_state"]
    permissions = ["device:read", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = ["sensor", "health", "calibration", "offline", "reading"]
        return any(kw in task.intent.lower() for kw in keywords)

    async def run(self, task: Task) -> Result:
        # Placeholder: real implementation would check last reading timestamps
        return Result(
            success=True,
            data={"healthy_sensors": 5, "offline_sensors": 0},
            message="Sensor health check complete",
        )
