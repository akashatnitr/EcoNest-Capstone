"""Read-only views of local EcoNest benchmark reports."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.config import get_settings
from orchestrator.core.database import healthcheck_arcadedb, healthcheck_mysql
from orchestrator.core.weekly_health import WEEKLY_HEALTH_ENTITIES

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])
settings = get_settings()

# These CPU-only figures were collected manually before the native-GPU switch.
# New CPU/GPU comparisons are sourced from the gitignored reports below.
MANUAL_VALIDATION_EVIDENCE: dict[str, Any] = {
    "captured_on": "2026-09-29",
    "configuration": [
        {
            "label": "Host configuration",
            "value": "Apple M4 · 16 GB RAM",
            "detail": "Docker memory ceiling: 7.75 GiB; macOS free-memory reading: 69%.",
        },
        {
            "label": "Configured local model",
            "value": "Gemma 3 4B",
            "detail": "Primary and fallback model. The original Docker CPU Ollama used keep-alive 0; native GPU Ollama is now the live endpoint.",
        },
        {
            "label": "Linux deployment posture",
            "value": "Linux containers verified",
            "detail": "EcoNest services run in Docker Desktop's Linux VM on this Mac; a native Linux-host deployment has not yet been benchmarked.",
        },
        {
            "label": "Earlier CPU model footprint",
            "value": "4.085 GiB",
            "detail": "Observed Ollama container memory while Gemma was retained in memory.",
        },
        {
            "label": "Earlier CPU full-stack footprint",
            "value": "≈6.96 GiB",
            "detail": "Orchestrator, Ollama, Home Assistant, MySQL, and ArcadeDB combined.",
        },
        {
            "label": "Earlier CPU Gemma throughput",
            "value": "16.7 tokens/s",
            "detail": "45-token warm response: 3.34 s total, including 0.21 s model load.",
        },
        {
            "label": "Earlier CPU cold response",
            "value": "3.23 s",
            "detail": "The model was unloaded first. Loading it into memory took 2.78 s; the short reply then took the remaining time.",
        },
        {
            "label": "Earlier CPU warm response",
            "value": "0.32 s",
            "detail": "The identical prompt was repeated while the model was already in memory, avoiding the cold-start wait.",
        },
    ],
    "workflows": [
        {
            "name": "Energy recommendation",
            "status": "verified",
            "detail": "Gemma received energy evidence and returned an advisory recommendation grounded in the dryer’s 1,803.3 W reading.",
        },
        {
            "name": "Security recommendation",
            "status": "verified",
            "detail": "Security review completed as advisory-only and reported a LOW status with no detected anomaly.",
        },
        {
            "name": "Simulated high-risk security alert",
            "status": "verified",
            "detail": "A simulated 2 AM garage-motion event was classified HIGH. EcoNest informed the user, made no external dispatch, and reported sms_sent: false.",
        },
        {
            "name": "Watering recommendation",
            "status": "verified",
            "detail": "Gemma used the next-24-hour rain forecast to recommend skipping scheduled watering; no valve was controlled.",
        },
        {
            "name": "Confirmed device action",
            "status": "verified",
            "detail": "EcoNest resolved the Study Room light entity and completed the user-confirmed action.",
        },
        {
            "name": "Prompt history",
            "status": "verified",
            "detail": "Completed prompts and replies are retained locally in the browser and can be removed one entry at a time.",
        },
    ],
    "cautions": [
        "The September 2026 memory and response figures are CPU-only historical measurements; they do not describe the current GPU configuration.",
        "Model-only CPU/GPU comparisons exclude EcoNest routing, database access, Home Assistant, and browser latency.",
        "macOS power figures are estimated CPU/GPU/ANE values, not wall-power measurements.",
        "Docker Desktop verifies Linux-container compatibility, not native Linux-host performance.",
        "Security findings are advisory-only: EcoNest does not send SMS messages, dispatch personnel, or control security systems.",
        "Gemma recommendation output is validated against unavailable tariff claims; deterministic evidence remains the fallback if validation fails.",
    ],
    "feedback": [
        {
            "workflow": "Energy recommendation",
            "rating": "3/5",
            "detail": "The dryer issue was identified, but the recommendation did not explain how to mitigate it.",
        },
        {
            "workflow": "Watering recommendation",
            "rating": "3/5",
            "detail": "Weather was considered, but retained watering history and the forecast rain hour were not included.",
        },
        {
            "workflow": "Simulated high-risk security alert",
            "rating": "4/5",
            "detail": "The risk was recognized; the desired policy is user notification only, without claimed SMS delivery or dispatch.",
        },
    ],
}


def _benchmark_directory() -> Path:
    """Return the gitignored directory used by the local benchmark script."""
    return Path(__file__).resolve().parents[2] / "econest_exports" / "benchmarks"


def _read_json_reports(directory: Path) -> dict[str, dict[str, Any]]:
    """Read the newest valid report for each scenario label."""
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("econest-benchmark-*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        label = payload.get("label")
        if isinstance(label, str) and label not in reports:
            reports[label] = payload
    return reports


def _read_comparison_report(directory: Path) -> dict[str, Any] | None:
    """Read the newest complete local Ollama CPU/GPU comparison."""
    paths = sorted(
        directory.glob("ollama-cpu-gpu-*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "ollama_cpu_gpu_comparison":
            continue
        backends = payload.get("backends")
        if not isinstance(backends, dict) or not all(
            isinstance(backends.get(name), dict) for name in ("cpu", "gpu")
        ):
            continue
        if backends["cpu"].get("model_digest") != backends["gpu"].get("model_digest"):
            continue
        return payload
    return None


def _read_workflow_report(directory: Path) -> dict[str, Any] | None:
    """Read the newest complete, read-only EcoNest workflow comparison."""
    paths = sorted(
        directory.glob("econest-workflow-cpu-gpu-*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "econest_cpu_gpu_workflow":
            continue
        backends = payload.get("backends")
        if isinstance(backends, dict) and all(
            isinstance(backends.get(name), dict) for name in ("cpu", "gpu")
        ):
            return payload
    return None


def _power_summary(path: Path) -> dict[str, Any] | None:
    """Summarize the combined SoC power samples in a powermetrics report."""
    if not path.is_file():
        return None
    values: list[int] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^Combined Power .*: (\d+) mW$", line)
            if match:
                values.append(int(match.group(1)))
    except OSError:
        return None
    if not values:
        return None
    return {
        "samples": len(values),
        "average_watts": round(mean(values) / 1000, 2),
        "minimum_watts": round(min(values) / 1000, 2),
        "maximum_watts": round(max(values) / 1000, 2),
        "complete": len(values) >= 300,
    }


def _container_summary(report: dict[str, Any] | None) -> list[dict[str, str]]:
    """Select browser-safe container fields from a benchmark report."""
    if not report:
        return []
    containers = report.get("docker", {}).get("containers", [])
    if not isinstance(containers, list):
        return []
    return [
        {
            "name": str(item.get("Name", "Unknown")),
            "memory": str(item.get("MemUsage", "—")),
            "cpu": str(item.get("CPUPerc", "—")),
        }
        for item in containers
        if isinstance(item, dict)
    ]


async def _weekly_health_report() -> dict[str, Any]:
    """Return the live, credential-safe checks used by the weekly report."""
    mysql_ok, arcadedb_ok = await healthcheck_mysql(), await healthcheck_arcadedb()
    services = [
        {"name": "MySQL", "status": "available" if mysql_ok else "unavailable"},
        {"name": "ArcadeDB", "status": "available" if arcadedb_ok else "unavailable"},
    ]
    states_by_entity: dict[str, dict[str, Any]] = {}
    home_assistant_error: str | None = None
    if not settings.HA_TOKEN:
        home_assistant_error = "Home Assistant token is not configured."
    else:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{settings.HA_URL.rstrip('/')}/api/states",
                    headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
                )
                response.raise_for_status()
                raw_states = response.json()
            if not isinstance(raw_states, list):
                raise ValueError("Home Assistant returned an invalid state list.")
            states_by_entity = {
                str(item.get("entity_id")): item
                for item in raw_states
                if isinstance(item, dict) and item.get("entity_id")
            }
        except (httpx.HTTPError, ValueError):
            home_assistant_error = "Home Assistant is unreachable or did not return device states."

    services.append(
        {
            "name": "Home Assistant",
            "status": "available" if home_assistant_error is None else "unavailable",
        }
    )
    devices: list[dict[str, str]] = []
    for name, entity_id in WEEKLY_HEALTH_ENTITIES.items():
        state = states_by_entity.get(entity_id)
        if state is None:
            availability = "not checked" if home_assistant_error else "not found"
            current_state = "—"
        else:
            current_state = str(state.get("state", "unknown"))
            availability = (
                "unavailable"
                if current_state == "unavailable"
                else "unknown"
                if current_state == "unknown"
                else "available"
            )
        devices.append(
            {"name": name, "availability": availability, "current_state": current_state}
        )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "services": services,
        "devices": devices,
        "available_devices": sum(item["availability"] == "available" for item in devices),
        "attention_devices": sum(item["availability"] != "available" for item in devices),
        "home_assistant_error": home_assistant_error,
    }


@router.get("", response_class=HTMLResponse)
async def benchmarks_page() -> HTMLResponse:
    """Serve the human-readable local benchmark dashboard."""
    page = Path(__file__).resolve().parents[1] / "static" / "benchmarks.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))


@router.get("/api/report")
async def benchmark_report() -> dict[str, Any]:
    """Return locally retained benchmark evidence without exposing raw files."""
    directory = _benchmark_directory()
    reports = _read_json_reports(directory) if directory.is_dir() else {}
    comparison = _read_comparison_report(directory) if directory.is_dir() else None
    workflow = _read_workflow_report(directory) if directory.is_dir() else None
    idle = reports.get("idle") or reports.get("idle-baseline-corrected") or reports.get("idle-baseline")
    warm = reports.get("warm-command")
    latest = max(reports.values(), key=lambda report: str(report.get("collected_at", "")), default=None)
    health = latest.get("health") if latest else None
    host = latest.get("host") if latest else None
    docker = latest.get("docker") if latest else None
    latency = warm.get("latency") if warm else None
    return {
        "available": bool(reports) or comparison is not None or workflow is not None,
        "latest_collected_at": latest.get("collected_at") if latest else None,
        "cpu_gpu_comparison": comparison,
        "workflow_comparison": workflow,
        "health": health,
        "host": host,
        "containers": _container_summary(latest),
        "latency": latency,
        "power": {
            "idle": _power_summary(directory / "power-idle.txt"),
            "warm_command": _power_summary(directory / "power-warm-command.txt"),
        },
        "weekly_health": await _weekly_health_report(),
        "scenarios": sorted(reports),
        "idle_captured_at": idle.get("collected_at") if idle else None,
        "manual_validation": MANUAL_VALIDATION_EVIDENCE,
    }
