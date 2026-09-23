"""Tests for privacy-filtered EcoNest fine-tuning dataset candidates."""

from orchestrator.training.dataset import (
    ReviewStatus,
    TrainingExample,
    build_review_examples,
    partition_approved_examples,
)


def test_build_review_examples_redacts_sensitive_values():
    events = [
        {
            "timestamp": "2026-09-23T12:00:00+00:00",
            "event_type": "task.completed",
            "agent": "device",
            "intent": "Turn off the media room light",
            "success": True,
            "confidence": 0.95,
            "payload": {
                "entity_id": "light.upstairs_media_light_1",
                "action": "turn_off",
                "domain": "light",
                "access_token": "must-not-appear",
                "user_id": "must-not-appear",
            },
        },
        {
            "timestamp": "2026-09-23T12:01:00+00:00",
            "event_type": "energy.recommendations.generated",
            "source": "http_api",
            "pricing": {"period": "off_peak", "api_key": "must-not-appear"},
            "recommendations": [
                {
                    "entity_id": "light.upstairs_media_light_1",
                    "action": "turn_off",
                    "reason": "No active motion.",
                    "password": "must-not-appear",
                }
            ],
        },
    ]

    examples = build_review_examples(events)

    assert len(examples) == 2
    assert examples[0].task_type == "command_interpretation"
    assert examples[0].review_status == ReviewStatus.NEEDS_HUMAN_REVIEW
    assert "access_token" not in str(examples)
    assert "must-not-appear" not in str(examples)
    assert examples[1].task_type == "energy_recommendation"


def test_partition_only_includes_human_approved_examples():
    approved = TrainingExample(
        example_id="0" * 64,
        task_type="command_interpretation",
        input={"user_request": "Turn off a light"},
        target={"intent_type": "device_action"},
        provenance={"event_type": "task.completed", "timestamp": "now"},
        review_status=ReviewStatus.APPROVED,
    )
    pending = approved.model_copy(
        update={"example_id": "f" * 64, "review_status": ReviewStatus.NEEDS_HUMAN_REVIEW}
    )

    split = partition_approved_examples([approved, pending], evaluation_percent=20)

    assert split.evaluation == [approved]
    assert split.train == []
