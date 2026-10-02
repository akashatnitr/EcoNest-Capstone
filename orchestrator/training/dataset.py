"""Build reviewable, privacy-filtered fine-tuning candidates from audit events.

This module intentionally produces *candidates*, not automatic training data.
An audit record can show that a service call succeeded while still being a poor
example of the decision EcoNest should learn. Only candidates explicitly marked
``approved`` may be included in a train/evaluation split.
"""

from __future__ import annotations

import hashlib
import json
import re
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
    "address",
    "latitude",
    "longitude",
    "location",
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
        "irrigation_recommendation",
        "autonomy_decision",
        "answer_correction",
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


def build_feedback_review_examples(records: list[dict[str, Any]]) -> list[TrainingExample]:
    """Create second-stage tuning candidates from evidence-reviewed corrections.

    Approval for runtime guidance is not approval to memorize time-sensitive facts
    or private household details in model weights. A curator must review these
    examples again before marking them approved for the train/evaluation split.
    """
    examples: list[TrainingExample] = []
    for index, record in enumerate(records):
        if record.get("review_status") != "approved" or not record.get("correction"):
            continue
        input_data = _sanitize({
            "question": str(record.get("prompt") or ""),
            "agent": str(record.get("agent") or ""),
            "evidence_checked_by_reviewer": str(record.get("evidence_note") or ""),
        })
        target = _sanitize({"answer": str(record["correction"])})
        examples.append(TrainingExample(
            example_id=_example_id("answer_correction", input_data, target, index),
            task_type="answer_correction",
            input=input_data,
            target=target,
            provenance={"source": "resident_feedback", "timestamp": str(record.get("updated_at") or "unknown")},
            review_note="Check privacy, time-sensitive facts, and source evidence before training approval.",
        ))
    return examples


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


def read_jsonl_examples(source: Path) -> list[TrainingExample]:
    """Load human-reviewed candidates from a local JSONL file."""
    examples: list[TrainingExample] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            examples.append(TrainingExample.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"Invalid training candidate on line {line_number}: {exc}") from exc
    return examples


def to_chat_examples(examples: list[TrainingExample]) -> list[dict[str, Any]]:
    """Convert approved review examples to Gemma supervised-chat JSONL records."""
    return [
        {
            "messages": [
                {"role": "system", "content": _system_instruction(example.task_type)},
                {
                    "role": "user",
                    "content": "EcoNest decision context:\n"
                    + json.dumps(example.input, ensure_ascii=False, sort_keys=True),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(example.target, ensure_ascii=False, sort_keys=True),
                },
            ]
        }
        for example in examples
    ]


def write_chat_jsonl(examples: list[TrainingExample], destination: Path) -> None:
    """Write approved examples in the chat JSONL format used by QLoRA tooling."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for example in to_chat_examples(examples):
            handle.write(json.dumps(example, ensure_ascii=False))
            handle.write("\n")


def _examples_from_event(event: dict[str, Any], index: int) -> list[TrainingExample]:
    event_type = str(event.get("event_type") or "")
    if event_type == "task.completed":
        example = _command_example(event, index)
        return [example] if example is not None else []
    if event_type == "energy.recommendations.generated":
        example = _recommendation_example(event, index, "energy_recommendation")
        return [example] if example is not None else []
    if event_type == "security.recommendations.generated":
        example = _recommendation_example(event, index, "security_recommendation")
        return [example] if example is not None else []
    if event_type == "irrigation.recommendations.generated":
        example = _recommendation_example(event, index, "irrigation_recommendation")
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


def _recommendation_example(
    event: dict[str, Any],
    index: int,
    task_type: Literal[
        "energy_recommendation", "security_recommendation", "irrigation_recommendation"
    ],
) -> TrainingExample | None:
    recommendations = event.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        return None
    cleaned = [_sanitize(item) for item in recommendations if isinstance(item, dict)]
    if not cleaned:
        return None

    return _new_example(
        event,
        index,
        task_type,
        {
            "pricing": _sanitize(event.get("pricing")),
            "household_routines": _sanitize(event.get("household_routines")),
            "source": _text(event.get("source")) or "unknown",
            "recommendation_kind": task_type.removesuffix("_recommendation"),
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
        "irrigation_recommendation",
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
    if isinstance(value, str):
        return re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-ip]", value)
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


def _system_instruction(task_type: str) -> str:
    """Keep a fixed safety contract across the QLoRA training examples."""
    if task_type == "command_interpretation":
        return (
            "You are EcoNest. Return only valid JSON. Infer an available device action "
            "from the supplied context, but always require explicit user confirmation "
            "before a device action."
        )
    if task_type == "answer_correction":
        return (
            "You are EcoNest. Return only valid JSON. Answer using the supplied verified "
            "evidence. State uncertainty when evidence is missing. Do not control devices."
        )
    if task_type == "autonomy_decision":
        return (
            "You are EcoNest. Return only valid JSON. Recommend only the supplied "
            "allowlisted action when the evidence supports it; otherwise do not act. "
            "External policy and verification remain mandatory."
        )
    return (
        "You are EcoNest. Return only valid JSON. Produce an advisory, evidence-grounded "
        "recommendation. Do not claim facts that are absent from the supplied context and "
        "do not control devices."
    )
