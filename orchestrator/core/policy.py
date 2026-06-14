"""Policy gates for autonomous EcoNest behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.config import get_settings


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating whether a task is allowed."""

    allowed: bool
    reason: str = ""


AUTONOMOUS_SOURCES = {
    "autonomous_monitor",
    "background_monitor",
    "scheduled_autonomy",
}


def evaluate_autonomous_action_policy(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    is_device_control: bool,
) -> PolicyDecision:
    """Block autonomous device actions unless explicitly enabled."""
    if not is_device_control:
        return PolicyDecision(allowed=True)

    source = str(metadata.get("source") or payload.get("source") or "")
    trigger = str(metadata.get("trigger") or payload.get("trigger") or "")
    is_autonomous = source in AUTONOMOUS_SOURCES or trigger in AUTONOMOUS_SOURCES
    if not is_autonomous:
        return PolicyDecision(allowed=True)

    if get_settings().AUTONOMY_ACTIONS_ENABLED:
        return PolicyDecision(allowed=True)

    return PolicyDecision(
        allowed=False,
        reason="Autonomous device actions require AUTONOMY_ACTIONS_ENABLED=true",
    )
