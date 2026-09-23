"""Build reviewable, privacy-filtered fine-tuning candidates from audit events.

This module intentionally produces *candidates*, not automatic training data.
An audit record can show that a service call succeeded while still being a poor
example of the decision EcoNest should learn. Only candidates explicitly marked
``approved`` may be included in a train/evaluation split.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "econest-training-v1"
_SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "authorization",
    "cookie",
    "email",
    "user_id",
)
_SENSITIVE_KEY_NAMES = {"api_key", "access_key", "private_key"}


class ReviewStatus(StrEnum):
    """Human review state of a candidate before it can be used for tuning."""

    NEEDS_HUMAN_REVIEW = "needs_human_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class TrainingExample(BaseModel):
    """One versioned supervised example for the EcoNest decision model."""

    schema_version: Literal["econest-training-v1"] = SCHEMA_VERSION
    example_id: str
    task_type: Literal[
        "command_interpretation",
        "energy_recommendation",
        "security_recommendation",
        "autonomy_decision",
    ]
    input: dict[str, Any]
    target: dict[str, Any]
    provenance: dict[str, str]
    review_status: ReviewStatus = ReviewStatus.NEEDS_HUMAN_REVIEW
    review_note: str | None = None


class DatasetSplit(BaseModel):
    """Approved examples partitioned deterministically for tuning and evaluation."""

    train: list[TrainingExample] = Field(default_factory=list)
    evaluation: list[TrainingExample] = Field(default_factory=list)


def build_review_examples(events: list[dict[str, Any]]) -> list[TrainingExample]:
    """Convert durable audit events into candidates for human review.

    The resulting examples are never automatically approved. This prevents a
    previous model hallucination, an unverified device action, or an operational
    failure from becoming a target the next model learns to imitate.
    """
    examples: list[TrainingExample] = []
    for index, event in enumerate(events):
        examples.extend(_examples_from_event(event, index))
    return examples


def partition_approved_examples(
    examples: list[TrainingExample], evaluation_percent: int = 20
) -> DatasetSplit:
    """Make a stable train/evaluation split from explicitly approved examples."""
    if not 1 <= evaluation_percent < 100:
        raise ValueError("evaluation_percent must be between 1 and 99")

    split = DatasetSplit()
    for example in examples:
        if example.review_status != ReviewStatus.APPROVED:
            continue
        bucket = int(example.example_id[:8], 16) % 100
        if bucket < evaluation_percent:
            split.evaluation.append(example)
        else:
            split.train.append(example)
    return split


def write_jsonl_examples(examples: list[TrainingExample], destination: Path) -> None:
    """Write examples to an explicit JSONL destination for offline review/training."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(example.model_dump_json())
            handle.write("\n")


def _examples_from_event(event: dict[str, Any], index: int) -> list[TrainingExample]:
    event_type = str(event.get("event_type") or "")
    if event_type == "task.completed":
        example = _command_example(event, index)
        return [example] if example is not None else []
    if event_type == "energy.recommendations.generated":
        example = _energy_example(event, index)
        return [example] if example is not None else []
    if event_type == "autonomy.action.recommended":
        example = _autonomy_example(event, index)
        return [example] if example is not None else []
    return []


def _command_example(
    event: dict[str, Any], index: int
) -> TrainingExample | None:
    payload = _mapping(event.get("payload"))
    entity_id = _text(payload.get("entity_id") or payload.get("device_id"))
    action = _text(payload.get("action"))
    intent = _text(event.get("intent"))
    if str(event.get("agent")) != "device" or not all((entity_id, action, intent)):
        return None

    succeeded = event.get("success") is True
    actual_outcome = event.get("actual_outcome")
    verified = actual_outcome is not False
    review_note = None
    if not succeeded or not verified:
        review_note = "Not automatically usable: the requested action was not verified."

    return _new_example(
        event,
        index,
        "command_interpretation",
        {
            "user_request": intent,
            "available_device": {
                "entity_id": entity_id,
                "domain": _text(payload.get("domain")) or entity_id.split(".", 1)[0],
            },
        },
        {
            "intent_type": "device_action",
            "entity_id": entity_id,
            "action": action,
            "confidence": _number(event.get("confidence")),
            "requires_confirmation": True,
            "verified_outcome": bool(succeeded and verified),
        },
        review_note,
    )


def _energy_example(event: dict[str, Any], index: int) -> TrainingExample | None:
    recommendations = event.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        return None
    cleaned = [_sanitize(item) for item in recommendations if isinstance(item, dict)]
    if not cleaned:
        return None

    return _new_example(
        event,
        index,
        "energy_recommendation",
        {
            "pricing": _sanitize(event.get("pricing")),
            "household_routines": _sanitize(event.get("household_routines")),
            "source": _text(event.get("source")) or "unknown",
        },
        {"recommendations": cleaned, "recommendation_only": True},
        "Review recommendation quality and factual grounding before approval.",
    )


def _autonomy_example(
    event: dict[str, Any], index: int
) -> TrainingExample | None:
    recommendation = _mapping(event.get("recommendation"))
    entity_id = _text(recommendation.get("entity_id") or event.get("entity_id"))
    action = _text(recommendation.get("action") or event.get("action"))
    if not entity_id or not action:
        return None

    return _new_example(
        event,
        index,
        "autonomy_decision",
        {
            "snapshot": _sanitize(event.get("snapshot")),
            "allowed_entity": entity_id,
            "allowed_action": action,
        },
        {
            "should_act": True,
            "entity_id": entity_id,
            "action": action,
            "confidence": _number(recommendation.get("confidence")),
            "risk_level": _text(recommendation.get("risk_level")),
            "reason": _text(recommendation.get("reason")),
        },
        "Review safety, state evidence, and execution outcome before approval.",
    )


def _new_example(
    event: dict[str, Any],
    index: int,
    task_type: Literal[
        "command_interpretation",
        "energy_recommendation",
        "security_recommendation",
        "autonomy_decision",
    ],
    input_data: dict[str, Any],
    target: dict[str, Any],
    review_note: str | None,
) -> TrainingExample:
    sanitized_input = _sanitize(input_data)
    sanitized_target = _sanitize(target)
    example_id = _example_id(task_type, sanitized_input, sanitized_target, index)
    return TrainingExample(
        example_id=example_id,
        task_type=task_type,
        input=sanitized_input,
        target=sanitized_target,
        provenance={
            "event_type": str(event.get("event_type") or "unknown"),
            "timestamp": str(event.get("timestamp") or "unknown"),
        },
        review_note=review_note,
    )


def _example_id(
    task_type: str, input_data: dict[str, Any], target: dict[str, Any], index: int
) -> str:
    material = json.dumps(
        {"task_type": task_type, "input": input_data, "target": target, "index": index},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _sanitize(value: Any) -> Any:
    """Recursively remove credentials and direct personal identifiers."""
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _SENSITIVE_KEY_NAMES
        or any(part in normalized for part in _SENSITIVE_KEY_PARTS)
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
