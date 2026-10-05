"""Short-lived, UI-safe trace data for active command tasks."""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

_MAX_TASKS = 200
_LOCK = Lock()
_TRACES: dict[str, list[dict[str, object]]] = {}
_TRACE_CONTEXT: ContextVar[tuple[str, str] | None] = ContextVar(
    "econest_execution_trace_context", default=None
)


def start_execution_trace(
    task_id: str,
    *,
    kind: Literal["tool", "resource", "model"],
    name: str,
    agent: str,
) -> str | None:
    """Start an in-memory capability trace and return its opaque identifier."""
    if not task_id:
        return None
    trace_id = str(uuid4())
    entry: dict[str, object] = {
        "id": trace_id,
        "kind": kind,
        "name": name,
        "agent": agent or "System",
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_monotonic": monotonic(),
        "duration_ms": None,
        "error": None,
    }
    with _LOCK:
        _TRACES.setdefault(task_id, []).append(entry)
        while len(_TRACES) > _MAX_TASKS:
            _TRACES.pop(next(iter(_TRACES)))
    return trace_id


def set_execution_trace_context(
    task_id: str, agent: str
) -> Token[tuple[str, str] | None]:
    """Associate direct model calls with the active task for this context."""
    return _TRACE_CONTEXT.set((task_id, agent))


def reset_execution_trace_context(token: Token[tuple[str, str] | None]) -> None:
    """Restore the prior task trace context."""
    _TRACE_CONTEXT.reset(token)


def start_model_execution_trace(model: str) -> tuple[str, str | None]:
    """Start a local-model trace when a task trace context is active."""
    context = _TRACE_CONTEXT.get()
    if context is None:
        return "", None
    task_id, agent = context
    return task_id, start_execution_trace(
        task_id,
        kind="model",
        name=f"Gemma model response ({model})",
        agent=agent,
    )


def finish_execution_trace(
    task_id: str,
    trace_id: str | None,
    *,
    success: bool,
    error: str = "",
) -> None:
    """Finish a previously started trace without affecting the capability call."""
    if not task_id or trace_id is None:
        return
    with _LOCK:
        for entry in _TRACES.get(task_id, []):
            if entry.get("id") != trace_id:
                continue
            started = entry.get("started_monotonic")
            elapsed = monotonic() - started if isinstance(started, float) else 0.0
            entry["status"] = "completed" if success else "failed"
            entry["duration_ms"] = round(elapsed * 1000, 3)
            entry["error"] = error[:300] if error else None
            return


def task_execution_trace(task_id: str) -> list[dict[str, object]]:
    """Return safe trace fields and live elapsed times for one task."""
    with _LOCK:
        entries = [dict(item) for item in _TRACES.get(task_id, [])]
    now = monotonic()
    trace: list[dict[str, object]] = []
    for entry in entries:
        started = entry.pop("started_monotonic", None)
        if entry.get("status") == "running" and isinstance(started, float):
            entry["duration_ms"] = round((now - started) * 1000, 3)
        entry.pop("id", None)
        trace.append(entry)
    return trace
