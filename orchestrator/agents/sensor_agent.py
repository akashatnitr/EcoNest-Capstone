"""Sensor health monitoring agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.agents.base import BaseAgent, Result, Task
from orchestrator.core.database import arcadedb_query
from orchestrator.llm.memory import get_recent_interactions

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "sensor.j2"


class SensorObservation(BaseModel):
    sensor_name: str
    value: float | None = None
    last_seen_minutes: int | None = None
    sensor_type: str | None = None


class SensorIssue(BaseModel):
    issue: str
    severity: str = Field(pattern="^(LOW|MEDIUM|HIGH)$")


class SensorHealthReport(BaseModel):
    healthy_sensors: int
    offline_sensors: int
    llm_assessment: str | None = None
    issues: list[SensorIssue] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


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

    async def run(
        self,
        task: Task,
    ) -> Result:
        context = await self._build_context(task)

        observations = self._observations_from_payload(task.payload)

        observations.extend(await self._observations_from_graph())

        issues = self._detect_issues(observations)

        recommendations = self._recommendations(issues)

        confidence = max(
            0.1,
            1.0 - (0.15 * len(issues)),
        )

        llm_note = await self._llm_assessment(
            task,
            issues,
            context,
        )

        report = SensorHealthReport(
            healthy_sensors=max(
                0,
                len(observations) - len(issues),
            ),
            offline_sensors=sum(
                1 for issue in issues if "offline" in issue.issue.lower()
            ),
            issues=issues,
            recommendations=recommendations,
            context=context,
            llm_assessment=llm_note,
        )

        return Result(
            success=True,
            confidence=confidence,
            data=report.model_dump(),
            message="Sensor health check complete",
        )

    def _observations_from_payload(
        self,
        payload: dict[str, Any],
    ) -> list[SensorObservation]:

        observations = []

        for sensor in payload.get(
            "sensors",
            [],
        ):
            observations.append(
                SensorObservation(
                    sensor_name=sensor.get(
                        "name",
                        "unknown",
                    ),
                    value=sensor.get("value"),
                    sensor_type=sensor.get("sensor_type"),
                    last_seen_minutes=sensor.get("last_seen_minutes"),
                )
            )

        return observations

    async def _observations_from_graph(
        self,
    ) -> list[SensorObservation]:

        try:
            result = await arcadedb_query(
                "sql",
                "SELECT FROM Sensor LIMIT 10",
            )
        except Exception:
            return []

        observations = []

        for row in result.get(
            "result",
            [],
        ):
            if isinstance(row, dict):
                observations.append(
                    SensorObservation(
                        sensor_name=str(
                            row.get(
                                "name",
                                "unknown",
                            )
                        )
                    )
                )

        return observations

    def _detect_issues(
        self,
        observations: list[SensorObservation],
    ) -> list[SensorIssue]:
        issues = []
        for obs in observations:

            if obs.last_seen_minutes and obs.last_seen_minutes > 15:
                issues.append(
                    SensorIssue(
                        issue=(f"{obs.sensor_name} offline"),
                        severity="HIGH",
                    )
                )

            if obs.value is not None and (obs.value < -100 or obs.value > 1000):
                issues.append(
                    SensorIssue(
                        issue=(f"{obs.sensor_name} value out of range"),
                        severity="MEDIUM",
                    )
                )
        temperature_sensors = [
            obs
            for obs in observations
            if obs.sensor_type == "temperature" and obs.value is not None
        ]
        if len(temperature_sensors) >= 2:
            values = [obs.value for obs in temperature_sensors if obs.value is not None]

            if max(values) - min(values) > 15:
                issues.append(
                    SensorIssue(
                        issue=(
                            "Temperature sensors show " "possible calibration drift"
                        ),
                        severity="MEDIUM",
                    )
                )

        return issues

    async def _build_context(
        self,
        task: Task,
    ) -> dict[str, Any]:

        try:
            recent = await get_recent_interactions(
                task.user_id,
                n=5,
            )

            interaction_count = len(recent)

        except Exception:
            interaction_count = 0

        return {
            "recent_interactions": interaction_count,
            "source": task.metadata.get(
                "source",
                "scheduled",
            ),
        }

    def _recommendations(
        self,
        issues: list[SensorIssue],
    ) -> list[str]:

        if not issues:
            return [
                "No action required",
            ]

        recommendations = []

        for issue in issues:

            issue_text = issue.issue.lower()

            if "offline" in issue_text:
                recommendations.append(
                    "Inspect power and connectivity",
                )

            elif "out of range" in issue_text:
                recommendations.append(
                    "Verify sensor calibration",
                )

            elif "calibration drift" in issue_text:
                recommendations.append(
                    "Recalibrate affected sensors",
                )

            else:
                recommendations.append(
                    issue.issue,
                )

        return recommendations

    async def _llm_assessment(
        self,
        task: Task,
        issues: list[SensorIssue],
        context: dict[str, Any],
    ) -> str | None:

        if task.payload.get("use_llm") is not True:
            return None
        template = PROMPT_PATH.read_text()
        prompt = _render_prompt(
            template,
            {
                "context": context,
                "observations": [],
                "issues": [issue.model_dump() for issue in issues],
            },
        )
        try:
            response = await self.llm.generate(
                prompt,
                temperature=0.2,
            )
        except Exception:
            return None
        response = response.strip()
        return response if response else None


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
