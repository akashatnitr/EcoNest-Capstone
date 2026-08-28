"""Durable JSONL audit logging for autonomous EcoNest behavior."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from orchestrator.config import get_settings
from orchestrator.core.database import mysql_session_context

_LOCK = threading.Lock()


def write_audit_event(
    event_type: str,
    payload: dict[str, Any],
    persist_mysql: bool = True,
) -> dict[str, Any] | None:
    """Append a structured audit event to the local JSONL log."""
    settings = get_settings()
    if not settings.AUDIT_LOG_ENABLED:
        return None

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
        with _LOCK:
            _rotate_if_needed(path)
    except Exception:
        return None
    if persist_mysql:
        _schedule_mysql_persist(event)
    return event


async def write_audit_event_async(
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Write an audit event to JSONL and persist it to MySQL when available."""
    event = write_audit_event(event_type, payload, persist_mysql=False)
    if event is not None:
        await persist_audit_event(event)
    return event


def _schedule_mysql_persist(event: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(persist_audit_event(event))


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


async def read_recent_audit_events_async(limit: int = 50) -> list[dict[str, Any]]:
    """Read durable audit history from MySQL, falling back to the local JSONL log."""
    bounded_limit = max(1, min(limit, 1_000))
    try:
        async with mysql_session_context() as session:
            result = await session.execute(
                text(
                    "SELECT payload FROM audit_events "
                    "ORDER BY event_time DESC, id DESC LIMIT :limit"
                ),
                {"limit": bounded_limit},
            )
            rows = result.mappings().all()
    except Exception:
        return read_recent_audit_events(bounded_limit)

    events = [_audit_payload_mapping(row.get("payload")) for row in rows]
    valid_events = [event for event in events if event is not None]
    return list(reversed(valid_events))


async def ensure_audit_table() -> None:
    """Create the MySQL audit table used for long-term analysis."""
    try:
        async with mysql_session_context() as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        event_time TIMESTAMP(6) NOT NULL,
                        event_type VARCHAR(100) NOT NULL,
                        task_id VARCHAR(64),
                        agent VARCHAR(100),
                        success BOOLEAN,
                        source VARCHAR(100),
                        payload JSON NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_audit_event_time (event_time),
                        INDEX idx_audit_event_type (event_type),
                        INDEX idx_audit_task_id (task_id),
                        INDEX idx_audit_agent (agent)
                    ) ENGINE=InnoDB
                    """
                )
            )
            await session.commit()
    except Exception:
        return


async def persist_audit_event(event: dict[str, Any]) -> None:
    """Persist one audit event to MySQL without breaking runtime behavior."""
    try:
        async with mysql_session_context() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (event_time, event_type, task_id, agent, success, source, payload)
                    VALUES
                        (:event_time, :event_type, :task_id, :agent, :success, :source, :payload)
                    """
                ),
                {
                    "event_time": _mysql_timestamp(event.get("timestamp")),
                    "event_type": str(event.get("event_type", ""))[:100],
                    "task_id": _optional_text(event.get("task_id"), 64),
                    "agent": _optional_text(event.get("agent"), 100),
                    "success": (
                        event.get("success")
                        if isinstance(event.get("success"), bool)
                        else None
                    ),
                    "source": _optional_text(event.get("source"), 100),
                    "payload": json.dumps(event, default=str, sort_keys=True),
                },
            )
            await session.commit()
    except Exception:
        return


def summarize_audit_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build simple long-term health metrics from recent audit events."""
    task_results = [
        event for event in events if event.get("event_type") == "task.completed"
    ]
    failed = [event for event in task_results if event.get("success") is False]
    routed_agents: dict[str, int] = {}
    suggestion_titles: dict[str, int] = {}
    feedback_count = 0

    for event in events:
        agent = event.get("agent")
        if isinstance(agent, str) and agent:
            routed_agents[agent] = routed_agents.get(agent, 0) + 1
        if event.get("event_type") == "feedback.generated":
            feedback_count += 1
            for title in _suggestion_titles(event):
                suggestion_titles[title] = suggestion_titles.get(title, 0) + 1

    completed_count = len(task_results)
    success_count = completed_count - len(failed)
    return {
        "event_count": len(events),
        "task_completed_count": completed_count,
        "task_success_count": success_count,
        "task_failed_count": len(failed),
        "task_success_rate": (
            round(success_count / completed_count, 3) if completed_count else None
        ),
        "feedback_count": feedback_count,
        "failed_actions": failed[-10:],
        "agent_routing": _top_counts(routed_agents),
        "common_suggestions": _top_counts(suggestion_titles),
        "latest_event": events[-1] if events else None,
    }


def _rotate_if_needed(path: Path) -> None:
    settings = get_settings()
    max_bytes = settings.AUDIT_LOG_MAX_BYTES
    if max_bytes <= 0 or not path.exists() or path.stat().st_size <= max_bytes:
        return

    backups = max(1, settings.AUDIT_LOG_BACKUP_COUNT)
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            if target.exists():
                target.unlink()
            source.rename(target)
    first_backup = path.with_name(f"{path.name}.1")
    if first_backup.exists():
        first_backup.unlink()
    shutil.move(str(path), str(first_backup))
    path.touch()


def _mysql_timestamp(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("T", " ").replace("+00:00", "")[:26]
    return datetime.now(timezone.utc).isoformat().replace("T", " ")[:26]


def _optional_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _audit_payload_mapping(value: Any) -> dict[str, Any] | None:
    """Normalize a JSON MySQL payload returned as a mapping or JSON string."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _suggestion_titles(event: dict[str, Any]) -> list[str]:
    suggestions = event.get("suggestions")
    if not isinstance(suggestions, list):
        return []
    titles = []
    for item in suggestions:
        if isinstance(item, dict) and isinstance(item.get("title"), str):
            titles.append(item["title"])
    return titles


def _top_counts(counts: dict[str, int], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:limit]
    ]
