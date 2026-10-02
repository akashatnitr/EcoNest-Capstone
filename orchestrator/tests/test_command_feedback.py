"""Tests for reviewed resident feedback and training boundaries."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.core.command_feedback import approved_correction_guidance
from orchestrator.training.dataset import build_feedback_review_examples, ReviewStatus


@pytest.mark.asyncio
async def test_only_same_user_same_agent_relevant_corrections_are_returned():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = [
        {"prompt": "How can I reduce dryer energy use?", "correction": "Compare the dryer to its baseline."},
        {"prompt": "When should I water the garden?", "correction": "Mention last watering."},
    ]
    session.execute.return_value = result
    guidance = await approved_correction_guidance(
        session, user_id=42, agent="energy", prompt="How can I reduce dryer energy use today?"
    )
    assert "Compare the dryer" in guidance
    assert "last watering" not in guidance
    assert "NOT current household facts" in guidance
    assert session.execute.await_args.args[1] == {"user_id": 42, "agent": "energy"}


def test_training_candidates_need_separate_review_even_after_runtime_approval():
    records = [
        {"prompt": "How much power?", "correction": "Use the current reading.",
         "review_status": "approved", "evidence_note": "Checked current sensor and timestamp.",
         "agent": "energy", "updated_at": "2026-09-30"},
        {"prompt": "Old question", "correction": "An unreviewed claim", "review_status": "needs_human_review"},
    ]
    examples = build_feedback_review_examples(records)
    assert len(examples) == 1
    assert examples[0].review_status == ReviewStatus.NEEDS_HUMAN_REVIEW
    assert examples[0].target == {"answer": "Use the current reading."}
