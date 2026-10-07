"""Tests for the local benchmark report page and API."""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def weekly_health_report(monkeypatch) -> None:
    """Keep report tests independent from local Home Assistant and databases."""
    monkeypatch.setattr(
        "orchestrator.api.benchmarks._weekly_health_report",
        AsyncMock(
            return_value={
                "checked_at": "2026-10-01T12:00:00+00:00",
                "services": [{"name": "MySQL", "status": "available"}],
                "devices": [
                    {
                        "name": "Study Room Lights",
                        "availability": "available",
                        "current_state": "off",
                    }
                ],
                "available_devices": 1,
                "attention_devices": 0,
                "home_assistant_error": None,
            }
        ),
    )


def test_benchmark_page_is_served(client) -> None:
    response = client.get("/benchmarks")

    assert response.status_code == 200
    assert "ECONEST / BENCHMARK REPORT" in response.text
    assert "CPU versus GPU" in response.text
    assert "Complete advisory request" in response.text
    assert "Browser timing on this device" in response.text
    assert "fetch('/health',{cache:'no-store'})" in response.text
    assert "newest saved measurement" in response.text
    assert "It does not run a new host benchmark" in response.text
    assert "${weeklyHealthSection(data)}${earlierCpuEvidence(data)}${overview(data,liveHealth)}" in response.text
    assert "Weekly health check" in response.text
    assert "Device availability" in response.text


def test_benchmark_api_handles_missing_reports(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("orchestrator.api.benchmarks._benchmark_directory", lambda: tmp_path)

    response = client.get("/benchmarks/api/report")

    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["manual_validation"]["configuration"]
    assert response.json()["weekly_health"]["available_devices"] == 1


def test_benchmark_api_includes_matched_cpu_gpu_report(client, monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setattr("orchestrator.api.benchmarks._benchmark_directory", lambda: tmp_path)
    report = {
        "kind": "ollama_cpu_gpu_comparison",
        "captured_at": "2026-10-02T00:19:42+00:00",
        "model": "gemma3:4b",
        "backends": {
            "cpu": {"model_digest": "same", "warm_median_total_ms": 4800},
            "gpu": {"model_digest": "same", "warm_median_total_ms": 2400},
        },
    }
    (tmp_path / "ollama-cpu-gpu-20261001-191942.json").write_text(json.dumps(report))

    response = client.get("/benchmarks/api/report")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["cpu_gpu_comparison"]["model"] == "gemma3:4b"


def test_benchmark_api_ignores_mismatched_models(client, monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setattr("orchestrator.api.benchmarks._benchmark_directory", lambda: tmp_path)
    report = {
        "kind": "ollama_cpu_gpu_comparison",
        "backends": {
            "cpu": {"model_digest": "one"},
            "gpu": {"model_digest": "different"},
        },
    }
    (tmp_path / "ollama-cpu-gpu-invalid.json").write_text(json.dumps(report))

    response = client.get("/benchmarks/api/report")

    assert response.status_code == 200
    assert response.json()["cpu_gpu_comparison"] is None


def test_benchmark_api_includes_workflow_comparison(client, monkeypatch, tmp_path) -> None:
    import json

    monkeypatch.setattr("orchestrator.api.benchmarks._benchmark_directory", lambda: tmp_path)
    report = {
        "kind": "econest_cpu_gpu_workflow",
        "captured_at": "2026-10-02T01:00:00+00:00",
        "backends": {
            "cpu": {"median_total_to_result_ms": 20000},
            "gpu": {"median_total_to_result_ms": 10000},
        },
    }
    (tmp_path / "econest-workflow-cpu-gpu-20261001-200000.json").write_text(json.dumps(report))

    response = client.get("/benchmarks/api/report")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["workflow_comparison"]["backends"]["gpu"]["median_total_to_result_ms"] == 10000
