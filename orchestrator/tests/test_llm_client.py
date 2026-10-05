"""Tests for local-model execution timing."""

from typing import Any

import pytest

from orchestrator.core.execution_trace import (
    reset_execution_trace_context,
    set_execution_trace_context,
    task_execution_trace,
)
from orchestrator.llm.client import LLMClient
from orchestrator.llm.models import LLMMessage


@pytest.mark.anyio
async def test_chat_records_model_timing_for_active_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient(model="gemma-test")

    async def fake_chat(*args: Any, **kwargs: Any) -> str:
        return "model response"

    monkeypatch.setattr(client, "_chat", fake_chat)
    token = set_execution_trace_context("llm-trace-task", "energy")
    try:
        assert (
            await client.chat([LLMMessage(role="user", content="test")])
            == "model response"
        )
    finally:
        reset_execution_trace_context(token)
        await client.close()

    trace = task_execution_trace("llm-trace-task")
    assert trace[0]["kind"] == "model"
    assert trace[0]["name"] == "Gemma model response (gemma-test)"
    assert trace[0]["status"] == "completed"
