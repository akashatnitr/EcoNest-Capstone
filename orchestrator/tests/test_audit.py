"""Tests for durable autonomous audit logging."""

from orchestrator.core import audit


class _AuditSettings:
    def __init__(self, path: str, enabled: bool = True) -> None:
        self.AUDIT_LOG_ENABLED = enabled
        self.AUDIT_LOG_PATH = path


def test_audit_log_writes_and_reads_recent_events(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "get_settings", lambda: _AuditSettings(str(path)))

    audit.write_audit_event("task.submitted", {"task_id": "task-1"})
    audit.write_audit_event("task.completed", {"task_id": "task-1", "success": True})

    events = audit.read_recent_audit_events(limit=1)

    assert len(events) == 1
    assert events[0]["event_type"] == "task.completed"
    assert events[0]["task_id"] == "task-1"
    assert events[0]["success"] is True
    assert "timestamp" in events[0]


def test_audit_log_respects_disabled_setting(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        audit,
        "get_settings",
        lambda: _AuditSettings(str(path), enabled=False),
    )

    audit.write_audit_event("agent.run", {"task_id": "task-1"})

    assert not path.exists()
