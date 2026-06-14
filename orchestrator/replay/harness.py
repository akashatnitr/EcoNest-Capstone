"""Replay stored Home Assistant snapshots through deterministic EcoNest checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator.api.demo import (
    FeedbackSuggestion,
    _build_household_feedback_snapshot,
    _fallback_periodic_feedback,
)


@dataclass(frozen=True)
class ReplayScenario:
    """A stored snapshot and the outcomes expected from EcoNest analysis."""

    name: str
    states: list[dict[str, Any]]
    expected_categories: set[str] = field(default_factory=set)
    expected_titles: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ReplayResult:
    """Result of replaying one scenario."""

    name: str
    passed: bool
    missing_categories: set[str]
    missing_titles: set[str]
    suggestions: list[FeedbackSuggestion]
    snapshot: dict[str, Any]


def replay_feedback_scenarios(scenarios: list[ReplayScenario]) -> list[ReplayResult]:
    """Run stored snapshots through deterministic feedback expectations."""
    return [replay_feedback_scenario(scenario) for scenario in scenarios]


def replay_feedback_scenario(scenario: ReplayScenario) -> ReplayResult:
    """Replay one snapshot and compare suggestions to expected outcomes."""
    snapshot = _build_household_feedback_snapshot(scenario.states)
    feedback = _fallback_periodic_feedback(snapshot)
    categories = {suggestion.category for suggestion in feedback.suggestions}
    titles = {suggestion.title for suggestion in feedback.suggestions}
    missing_categories = scenario.expected_categories - categories
    missing_titles = scenario.expected_titles - titles
    return ReplayResult(
        name=scenario.name,
        passed=not missing_categories and not missing_titles,
        missing_categories=missing_categories,
        missing_titles=missing_titles,
        suggestions=feedback.suggestions,
        snapshot=snapshot,
    )
