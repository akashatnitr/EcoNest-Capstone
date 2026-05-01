"""Security monitoring agent."""

from orchestrator.agents.base import BaseAgent, Result, Task


class SecurityAgent(BaseAgent):
    """Responsibilities: intrusion detection, anomaly classification, SMS alerts."""

    name = "security"
    tools = ["query_mysql", "ha_get_state", "send_sms", "query_arcadedb"]
    permissions = ["device:read", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = ["security", "motion", "intrusion", "alert", "garage", "night"]
        return any(kw in task.intent.lower() for kw in keywords)

    async def run(self, task: Task) -> Result:
        # Placeholder: real implementation would check HA states and classify
        return Result(
            success=True,
            data={"severity": "LOW", "alert": "No anomalies detected"},
            message="Security check complete",
        )
