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
        allowlist_decision = _evaluate_autonomous_allowlist(payload)
        if not allowlist_decision.allowed:
            return allowlist_decision
        return PolicyDecision(allowed=True)

    return PolicyDecision(
        allowed=False,
        reason="Autonomous device actions require AUTONOMY_ACTIONS_ENABLED=true",
    )


def _evaluate_autonomous_allowlist(payload: dict[str, Any]) -> PolicyDecision:
    settings = get_settings()
    domain = str(payload.get("domain") or "").lower()
    action = str(payload.get("action") or "").lower()
    entity_id = str(payload.get("entity_id") or payload.get("device_id") or "")
    action_key = f"{domain}.{action}"

    allowed_actions = _csv_set(settings.AUTONOMY_ALLOWED_ACTIONS)
    if action_key not in allowed_actions:
        return PolicyDecision(
            allowed=False,
            reason=f"Autonomous action {action_key} is not allowlisted",
        )

    allowed_entities = _csv_set(settings.AUTONOMY_ALLOWED_ENTITIES)
    if not allowed_entities:
        return PolicyDecision(
            allowed=False,
            reason="Autonomous actions require at least one allowlisted entity",
        )
    if entity_id not in allowed_entities:
        return PolicyDecision(
            allowed=False,
            reason=f"Autonomous entity {entity_id} is not allowlisted",
        )

    return PolicyDecision(allowed=True)


def _csv_set(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}
