"""Durable JSONL audit logging for autonomous EcoNest behavior."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.config import get_settings

_LOCK = threading.Lock()


def write_audit_event(event_type: str, payload: dict[str, Any]) -> None:
    """Append a structured audit event to the local JSONL log."""
    settings = get_settings()
    if not settings.AUDIT_LOG_ENABLED:
        return

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
    }
    path = Path(settings.AUDIT_LOG_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str, sort_keys=True))
            handle.write("\n")
    except Exception:
        return


def read_recent_audit_events(limit: int = 50) -> list[dict[str, Any]]:
    """Read the most recent audit events without failing the API."""
    path = Path(get_settings().AUDIT_LOG_PATH)
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events
