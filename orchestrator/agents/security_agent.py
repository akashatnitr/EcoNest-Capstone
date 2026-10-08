"""Security monitoring agent."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.agents.base import BaseAgent, Result, Task

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "security.j2"


class SecurityObservation(BaseModel):
    sensor: str
    observation_type: str
    value: str
    timestamp: str | None = None


class SecurityIncident(BaseModel):
    reason: str
    severity: str = Field(pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")


class SecurityAgentOutput(BaseModel):
    severity: str
    incidents: list[SecurityIncident] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    sms_sent: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class SecurityAgent(BaseAgent):
    """Responsibilities: intrusion detection and user-facing risk assessment."""

    name = "security"
    tools = ["query_mysql", "ha_get_state", "query_arcadedb"]
    permissions = ["device:read", "agent:run"]

    async def can_handle(self, task: Task) -> bool:
        keywords = [
            "security",
            "motion",
            "intrusion",
            "alert",
            "garage",
            "night",
            "occupancy",
            "sound",
        ]
        return (
            any(kw in task.intent.lower() for kw in keywords)
            or task.payload.get("type") == "security"
        )

    async def run(self, task: Task) -> Result:

        context = await self._build_context(task)
        observations = self._observations_from_payload(task.payload)
        observations.extend(await self._observations_from_graph(task))
        if not observations:
            unavailable = (
                "No retained motion-sensor observations are available for this period, "
                "so EcoNest cannot confirm recent motion activity."
                if "motion" in task.intent.lower()
                else "No retained security-sensor observations are available for this period, "
                "so EcoNest cannot confirm that the home is secure."
            )
            return Result(
                success=True,
                confidence=0.5,
                data={
                    "severity": "LOW",
                    "incidents": [],
                    "recommendations": [unavailable],
                    "sms_sent": False,
                    "context": context,
                },
                message="Security assessment complete",
            )
        incidents = self._classify_incidents(
            observations,
            context,
        )
        severity = self._highest_severity(incidents)
        recommendations = self._recommendations(
            severity,
            incidents,
        )
        llm_note = await self._llm_assessment(
            task,
            severity,
            incidents,
            context,
        )
        if llm_note:
            recommendations.insert(0, llm_note)

        confidence = min(
            1.0,
            0.5 + 0.2 * len(incidents),
        )

        output = SecurityAgentOutput(
            severity=severity,
            incidents=incidents,
            recommendations=recommendations,
            # This agent is advisory-only. It does not send SMS messages or
            # dispatch responders, so the field must never imply otherwise.
            sms_sent=False,
            context=context,
        )

        return Result(
            success=True,
            confidence=confidence,
            data=output.model_dump(),
            message="Security assessment complete",
        )

    async def _build_context(self, task: Task) -> dict[str, Any]:
        """Build contextual information for a security assessment."""
        current_hour = task.payload.get("current_hour")
        if current_hour is None:
            current_hour = datetime.now().hour

        context: dict[str, Any] = {
            "hour": current_hour,
            "source": task.metadata.get("source", ""),
            "trigger": task.metadata.get("event_type")
            or task.payload.get("event_type", ""),
        }

        # Read recent episodic memory when available.
        try:
            memory = await self.read_mcp_resource(task, "home://memory/recent")
            interactions = memory.get("recent_interactions", [])
            context["recent_interactions"] = (
                len(interactions) if isinstance(interactions, list) else 0
            )
        except Exception:
            context["recent_interactions"] = 0

        # For autonomous HA events, fetch the live state of the triggering entity
        # through MCP rather than trusting only the event payload.
        entity_id = str(task.payload.get("entity_id") or "").strip()
        if entity_id:
            try:
                live_state = await self.invoke_mcp_tool(
                    task,
                    "ha_get_state",
                    {"entity_id": entity_id},
                )

                if live_state.success:
                    context["live_entity_state"] = live_state.result
                else:
                    context["live_entity_state"] = {
                        "available": False,
                        "warnings": live_state.warnings,
                    }
            except Exception:
                context["live_entity_state"] = {
                    "available": False,
                    "warnings": ["Live Home Assistant state unavailable"],
                }

        return context

    def _observations_from_payload(
        self,
        payload: dict[str, Any],
    ) -> list[SecurityObservation]:

        observations: list[SecurityObservation] = []

        if payload.get("motion"):
            observations.append(
                SecurityObservation(
                    sensor="motion_sensor",
                    observation_type="motion",
                    value="detected",
                )
            )

        if payload.get("sound"):
            observations.append(
                SecurityObservation(
                    sensor="sound_sensor",
                    observation_type="sound",
                    value="detected",
                )
            )

        garage_minutes = payload.get("garage_open_minutes")

        if garage_minutes is not None:
            observations.append(
                SecurityObservation(
                    sensor="garage_door",
                    observation_type="garage",
                    value=str(garage_minutes),
                )
            )

        if payload.get("unknown_occupancy"):
            observations.append(
                SecurityObservation(
                    sensor="occupancy_detector",
                    observation_type="occupancy",
                    value="unknown",
                )
            )

        return observations

    async def _observations_from_graph(
        self,
        task: Task,
    ) -> list[SecurityObservation]:

        try:
            result = await self.invoke_mcp_tool(
                task,
                "query_arcadedb",
                {
                    "language": "sql",
                    "query": (
                        "SELECT * "
                        "FROM Observation "
                        "WHERE observation_type IN "
                        "('motion','sound','occupancy') "
                        "LIMIT 20"
                    ),
                },
            )
        except Exception:
            return []
        if not result.success:
            return []

        observations = []

        for row in result.result or []:
            if isinstance(row, dict):
                observations.append(
                    SecurityObservation(
                        sensor=str(
                            row.get(
                                "source_sensor",
                                "unknown",
                            )
                        ),
                        observation_type=str(
                            row.get(
                                "observation_type",
                                "unknown",
                            )
                        ),
                        value=str(
                            row.get(
                                "value",
                                "",
                            )
                        ),
                    )
                )

        return observations

    def _classify_incidents(
        self,
        observations: list[SecurityObservation],
        context: dict[str, Any],
    ) -> list[SecurityIncident]:

        incidents: list[SecurityIncident] = []

        hour = context["hour"]

        motion_seen = False
        sound_seen = False

        for observation in observations:

            if observation.observation_type == "motion":
                motion_seen = True

                if hour >= 23 or hour <= 6:
                    incidents.append(
                        SecurityIncident(
                            reason="Motion detected during quiet hours",
                            severity="HIGH",
                        )
                    )

            elif observation.observation_type == "sound":
                sound_seen = True

                if hour >= 23 or hour <= 6:
                    incidents.append(
                        SecurityIncident(
                            reason="Unexpected sound during quiet hours",
                            severity="HIGH",
                        )
                    )

            elif observation.observation_type == "garage":
                try:
                    duration = int(observation.value)
                except ValueError:
                    duration = 0

                if duration > 45:
                    incidents.append(
                        SecurityIncident(
                            reason="Garage door open longer than 45 minutes",
                            severity="HIGH",
                        )
                    )

            elif observation.observation_type == "occupancy":
                incidents.append(
                    SecurityIncident(
                        reason="Occupancy pattern differs from history",
                        severity="MEDIUM",
                    )
                )

        if motion_seen and sound_seen:
            incidents.append(
                SecurityIncident(
                    reason="Motion and sound detected simultaneously",
                    severity="CRITICAL",
                )
            )

        if not incidents:
            incidents.append(
                SecurityIncident(
                    reason="No security anomaly detected",
                    severity="LOW",
                )
            )

        return incidents

    def _highest_severity(
        self,
        incidents: list[SecurityIncident],
    ) -> str:

        ranking = {
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
        }

        return max(
            incidents,
            key=lambda item: ranking[item.severity],
        ).severity

    def _recommendations(
        self,
        severity: str,
        incidents: list[SecurityIncident],
    ) -> list[str]:

        if severity == "CRITICAL":
            return [
                "Critical security alert detected. Review the event in EcoNest and follow your household safety plan.",
            ]

        if severity == "HIGH":
            return [
                "High-priority security alert detected. Review the event in EcoNest and decide whether follow-up is needed.",
            ]

        if severity == "MEDIUM":
            return [
                "Security observation detected. Review the event in EcoNest when convenient.",
            ]

        return [
            "No security anomaly detected. No follow-up is suggested.",
        ]

    async def _llm_assessment(
        self,
        task: Task,
        severity: str,
        incidents: list[SecurityIncident],
        context: dict[str, Any],
    ) -> str | None:

        if task.payload.get("use_llm") is not True:
            return None

        if not PROMPT_PATH.exists():
            return None

        template = PROMPT_PATH.read_text()

        prompt = _render_prompt(
            template,
            {
                "context": context,
                "observations": [incident.model_dump() for incident in incidents],
                "anomalies": [incident.model_dump() for incident in incidents],
                "severity": severity,
            },
        )
        prompt += self.calendar_context_guidance(task)
        prompt += await self.reviewed_feedback_guidance(task)

        try:
            response = await self.llm.generate(
                prompt,
                temperature=0.2,
            )
        except Exception:
            return None

        response = _clean_llm_assessment(response)
        if not response or _suggests_unavailable_security_action(response):
            return None

        return response


def _clean_llm_assessment(response: str) -> str:
    """Keep an advisory assessment readable in the plain-text command UI."""

    return " ".join(
        response.replace("**", "").replace("`", "").strip().split()
    )


def _suggests_unavailable_security_action(response: str) -> bool:
    """Reject LLM text that claims or directs an external action EcoNest cannot take."""

    unsupported_phrases = (
        "dispatch",
        "security personnel",
        "security team",
        "emergency response",
        "send an sms",
        "sms sent",
        "text message sent",
        "call the homeowner",
        "contact the homeowner",
        "activate remote monitoring",
        "secure the perimeter",
    )
    lowered = response.lower()
    return any(phrase in lowered for phrase in unsupported_phrases)


def _render_prompt(
    template: str,
    values: dict[str, Any],
) -> str:
    rendered = template

    for key, value in values.items():
        rendered = rendered.replace(
            "{{ " + key + " }}",
            json.dumps(value, default=str),
        )
        rendered = rendered.replace(
            "{{" + key + "}}",
            json.dumps(value, default=str),
        )

    return rendered
