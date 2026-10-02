#!/usr/bin/env python3
"""Capture reproducible EcoNest host, container, storage, and latency metrics.

This script is intended to run on the machine hosting Docker. It never calls a
Home Assistant control service. Optional latency samples only submit a command
for interpretation, which stops before confirmation and device control.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_URL = "http://127.0.0.1:8001"
DEFAULT_PROMPT = "Turn off the media room light"
CONTAINERS = (
    "econest-real-orchestrator",
    "econest-real-ollama",
    "econest-real-homeassistant",
    "homeassistant",
    "econest-real-mysql",
    "econest-real-arcadedb",
)


def run_command(command: list[str]) -> tuple[str | None, str | None]:
    """Return command output without failing the entire benchmark."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return completed.stdout.strip(), None


def host_metrics() -> dict[str, Any]:
    """Collect portable host facts plus best-effort platform memory data."""
    disk = shutil.disk_usage(Path.cwd())
    metrics: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": __import__("os").cpu_count(),
        "disk_total_gb": round(disk.total / 2**30, 2),
        "disk_used_gb": round(disk.used / 2**30, 2),
        "disk_free_gb": round(disk.free / 2**30, 2),
    }
    if platform.system() == "Darwin":
        output, error = run_command(["vm_stat"])
        metrics["memory_command"] = "vm_stat"
        metrics["memory_raw"] = output if output else error
    else:
        output, error = run_command(["free", "-b"])
        metrics["memory_command"] = "free -b"
        metrics["memory_raw"] = output if output else error
    return metrics


def docker_metrics() -> dict[str, Any]:
    """Capture one non-streaming Docker resource snapshot and storage summary."""
    running_names, names_error = run_command(["docker", "ps", "--format", "{{.Names}}"])
    running = set(running_names.splitlines()) if running_names else set()
    targets = [name for name in CONTAINERS if name in running]
    stats: str | None = None
    stats_error: str | None = names_error
    if targets:
        stats, stats_error = run_command(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *targets]
        )
    elif not stats_error:
        stats_error = "No known EcoNest containers are running."
    rows: list[dict[str, str]] = []
    if stats:
        for line in stats.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append({str(key): str(value) for key, value in item.items()})
    system_df, system_df_error = run_command(["docker", "system", "df"])
    ollama_models, ollama_error = run_command(
        ["docker", "exec", "econest-real-ollama", "ollama", "list"]
    )
    return {
        "target_containers": targets,
        "containers": rows,
        "stats_error": stats_error,
        "docker_system_df": system_df,
        "docker_system_df_error": system_df_error,
        "ollama_models": ollama_models,
        "ollama_models_error": ollama_error,
    }


def health(url: str) -> dict[str, Any]:
    """Read the orchestrator health endpoint."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=15) as response:
            body = response.read().decode("utf-8")
    except (OSError, urllib.error.URLError) as exc:
        return {"reachable": False, "error": str(exc)}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body}
    return {"reachable": True, "response": payload}


def latency_sample(url: str, prompt: str) -> dict[str, Any]:
    """Measure safe command interpretation latency without executing a device action."""
    request = urllib.request.Request(
        f"{url.rstrip('/')}/command/interpret",
        data=json.dumps({"intent": prompt}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8"))
            http_status = response.status
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc), "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    return {
        "ok": 200 <= http_status < 300,
        "http_status": http_status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "result_status": payload.get("status"),
        "request_kind": payload.get("request_kind"),
    }


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    """Write a JSON report and a compact container CSV beside it."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"econest-benchmark-{stamp}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    rows = report["docker"]["containers"]
    if rows:
        csv_path = json_path.with_suffix(".containers.csv")
        fields = sorted({key for row in rows for key in row})
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return json_path


def main() -> int:
    """Run the requested benchmark collection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Orchestrator URL")
    parser.add_argument("--label", default="manual", help="Scenario label, e.g. idle or warm")
    parser.add_argument("--latency-samples", type=int, default=0, help="Safe interpretation samples")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt used for latency samples")
    parser.add_argument("--output-dir", type=Path, default=Path("econest_exports/benchmarks"))
    args = parser.parse_args()
    if args.latency_samples < 0 or args.latency_samples > 30:
        parser.error("--latency-samples must be between 0 and 30")

    samples = [latency_sample(args.url, args.prompt) for _ in range(args.latency_samples)]
    successful = [sample["latency_ms"] for sample in samples if sample.get("ok")]
    report = {
        "collected_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "orchestrator_url": args.url,
        "health": health(args.url),
        "host": host_metrics(),
        "docker": docker_metrics(),
        "latency": {
            "prompt": args.prompt if args.latency_samples else None,
            "samples": samples,
            "average_ms": round(sum(successful) / len(successful), 1) if successful else None,
        },
    }
    path = write_report(report, args.output_dir)
    print(f"Benchmark report: {path}")
    print(f"Health reachable: {report['health']['reachable']}")
    print(f"Container snapshots: {len(report['docker']['containers'])}")
    if successful:
        print(f"Average interpretation latency: {report['latency']['average_ms']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
