"""Tests for durable autonomous audit logging."""

from orchestrator.core import audit


class _AuditSettings:
    def __init__(
        self,
        path: str,
        enabled: bool = True,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
    ) -> None:
        self.AUDIT_LOG_ENABLED = enabled
        self.AUDIT_LOG_PATH = path
        self.AUDIT_LOG_MAX_BYTES = max_bytes
        self.AUDIT_LOG_BACKUP_COUNT = backup_count


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


def test_audit_log_rotates_when_size_limit_is_reached(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        audit,
        "get_settings",
        lambda: _AuditSettings(str(path), max_bytes=80, backup_count=2),
    )

    audit.write_audit_event("task.submitted", {"task_id": "task-1", "data": "x" * 80})

    assert path.exists()
    assert path.with_name("audit.jsonl.1").exists()


def test_audit_summary_calculates_operational_metrics():
    summary = audit.summarize_audit_events(
        [
            {
                "event_type": "task.completed",
                "task_id": "task-1",
                "agent": "device",
                "success": True,
            },
            {
                "event_type": "task.completed",
                "task_id": "task-2",
                "agent": "energy",
                "success": False,
            },
            {
                "event_type": "feedback.generated",
                "suggestions": [{"title": "Review current load"}],
            },
        ]
    )

    assert summary["task_completed_count"] == 2
    assert summary["task_success_rate"] == 0.5
    assert summary["task_failed_count"] == 1
    assert summary["agent_routing"][0] == {"name": "device", "count": 1}
    assert summary["common_suggestions"] == [
        {"name": "Review current load", "count": 1}
    ]


def test_audit_payload_mapping_accepts_mysql_json_payloads() -> None:
    event = audit._audit_payload_mapping(
        '{"event_type":"energy.recommendations.generated","task_id":"energy-1"}'
    )

    assert event == {
        "event_type": "energy.recommendations.generated",
        "task_id": "energy-1",
    }
    assert audit._audit_payload_mapping("not-json") is None
