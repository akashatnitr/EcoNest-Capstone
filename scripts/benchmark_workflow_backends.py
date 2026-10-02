#!/usr/bin/env python3
"""Benchmark a read-only EcoNest request through CPU and GPU backends.

Run inside the orchestrator container. A temporary loopback-only API instance
is started for each backend with ingestion, graph sync, autonomy, and device
actions disabled. The benchmark asks for an advisory-only energy review; it
never submits a device-control task. Results omit household answers and IDs.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROMPT = "Give me an energy recommendation for my home right now."
BACKENDS = {
    "cpu": "http://ollama:11434",
    "gpu": "http://host.docker.internal:11434",
}


def _request(url: str, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send one JSON request to a local API and return its response."""
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{url.rstrip('/')}{route}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected JSON from {route}")
    return data


def _trace_events(path: Path, offset: int) -> list[dict[str, Any]]:
    """Read only events appended during one benchmark request."""
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(offset)
        lines = handle.readlines()
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _offset(path: Path) -> int:
    """Return the current byte offset of a trace file."""
    return path.stat().st_size if path.exists() else 0


def _sum_ms(events: list[dict[str, Any]], event_type: str) -> float:
    """Sum matching instrumented durations, excluding malformed entries."""
    return round(sum(
        float(item.get("duration_ms", 0))
        for item in events
        if item.get("event_type") == event_type
        and isinstance(item.get("duration_ms"), (int, float))
    ), 1)


def _prewarm(url: str, model: str) -> None:
    """Load the same model before each measured request."""
    _request(url, "/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": "5m"})


def _unload(url: str, model: str) -> None:
    """Release a model after a backend's measurements."""
    _request(url, "/api/generate", {"model": model, "keep_alive": 0})


def _run_one(api_url: str, audit_path: Path, llm_path: Path) -> dict[str, Any]:
    """Measure advisory routing, async agent work, retrieval, and polling."""
    audit_start = _offset(audit_path)
    llm_start = _offset(llm_path)
    started = time.perf_counter()
    interpretation = _request(api_url, "/command/interpret", {"intent": PROMPT})
    interpret_ms = round((time.perf_counter() - started) * 1000, 1)
    if interpretation.get("status") != "advisory_started":
        raise ValueError(f"Unsafe or unexpected interpretation status: {interpretation.get('status')}")
    if interpretation.get("request_kind") != "energy_recommendation":
        raise ValueError(f"Unexpected request kind: {interpretation.get('request_kind')}")
    task_id = interpretation.get("task_id")
    if not isinstance(task_id, str):
        raise ValueError("Interpretation did not return a task ID")

    poll_count = 0
    poll_http_ms = 0.0
    while time.perf_counter() - started < 240:
        poll_count += 1
        poll_started = time.perf_counter()
        outcome = _request(api_url, f"/command/task/{task_id}")
        poll_http_ms += (time.perf_counter() - poll_started) * 1000
        if outcome.get("status") != "running":
            break
        time.sleep(1)
    else:
        raise TimeoutError("Advisory EcoNest task did not finish within 240 seconds")
    total_ms = round((time.perf_counter() - started) * 1000, 1)
    if outcome.get("status") != "completed" or outcome.get("agent") != "energy":
        raise ValueError(f"Advisory task failed or used an unexpected agent: {outcome.get('status')}")

    audit = [item for item in _trace_events(audit_path, audit_start) if item.get("task_id") == task_id]
    resources = [item for item in _trace_events(audit_path, audit_start)
                 if item.get("event_type") == "mcp.resource.read" and item.get("agent") == "command_interpreter"]
    llm = _trace_events(llm_path, llm_start)
    agent = next((item for item in audit if item.get("event_type") == "agent.run"), {})
    return {
        "status": "completed",
        "request_kind": interpretation["request_kind"],
        "agent": "energy",
        "interpret_ms": interpret_ms,
        "total_to_result_ms": total_ms,
        "agent_run_ms": round(float(agent.get("duration_ms", 0)), 1),
        "mcp_resource_ms": _sum_ms(resources, "mcp.resource.read"),
        "mcp_tool_ms": _sum_ms(audit, "mcp.tool.executed"),
        "llm_calls": len(llm),
        "llm_ms": _sum_ms(llm, "llm.call"),
        "llm_success_count": sum(item.get("success") is True for item in llm),
        "poll_count": poll_count,
        "poll_http_ms": round(poll_http_ms, 1),
    }


def _wait_for_server(api_url: str, process: subprocess.Popen[bytes]) -> None:
    """Wait for a temporary API server to accept loopback requests."""
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Temporary benchmark API exited during startup")
        try:
            result = _request(api_url, "/command/access")
            if result.get("authentication_required") is False:
                return
            raise RuntimeError("Temporary benchmark API did not disable authentication")
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise TimeoutError("Temporary benchmark API did not start")


def _run_backend(name: str, model: str, count: int, output_dir: Path, port: int) -> dict[str, Any]:
    """Start one isolated API process and benchmark one safe query repeatedly."""
    url = BACKENDS[name]
    api_url = f"http://127.0.0.1:{port}"
    audit_path = output_dir / f"workflow-{name}-audit.jsonl"
    llm_path = output_dir / f"workflow-{name}-llm.jsonl"
    server_log = output_dir / f"workflow-{name}-server.log"
    environment = os.environ.copy()
    environment.update({
        "OLLAMA_URL": url,
        "OLLAMA_MODEL": model,
        "OLLAMA_FALLBACK_MODEL": model,
        "HA_INGEST_ENABLED": "false",
        "GRAPH_SYNC_ENABLED": "false",
        "AUTONOMY_MONITOR_ENABLED": "false",
        "AUTONOMY_ACTIONS_ENABLED": "false",
        "HA_EVENT_DISPATCH_ENABLED": "false",
        "COMMAND_CENTER_AUTH_REQUIRED": "false",
        "AUDIT_LOG_PATH": str(audit_path),
        "BENCHMARK_LLM_TRACE_PATH": str(llm_path),
    })
    with server_log.open("ab") as log_handle:
        process = subprocess.Popen(
            [sys.executable, __file__, "--serve", "--port", str(port)],
            env=environment,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(api_url, process)
            samples = []
            for _ in range(count):
                _prewarm(url, model)
                samples.append(_run_one(api_url, audit_path, llm_path))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            _unload(url, model)
    return {
        "backend": name,
        "sample_count": len(samples),
        "samples": samples,
        "median_interpret_ms": round(statistics.median(item["interpret_ms"] for item in samples), 1),
        "median_total_to_result_ms": round(statistics.median(item["total_to_result_ms"] for item in samples), 1),
        "median_agent_run_ms": round(statistics.median(item["agent_run_ms"] for item in samples), 1),
        "median_mcp_resource_ms": round(statistics.median(item["mcp_resource_ms"] for item in samples), 1),
        "median_mcp_tool_ms": round(statistics.median(item["mcp_tool_ms"] for item in samples), 1),
        "median_llm_ms": round(statistics.median(item["llm_ms"] for item in samples), 1),
    }


def _serve(port: int) -> int:
    """Serve the temporary API with benchmark-only LLM timing instrumentation."""
    import uvicorn

    from orchestrator.core import audit
    from orchestrator.llm.client import LLMClient

    original_audit = audit.write_audit_event

    def local_audit(event_type: str, payload: dict[str, Any], persist_mysql: bool = True) -> dict[str, Any] | None:
        """Keep benchmark audit traces in local files, not the household database."""
        return original_audit(event_type, payload, persist_mysql=False)

    async def no_database_audit(event: dict[str, Any]) -> None:
        """Suppress MySQL audit persistence in this temporary process only."""
        return None

    audit.write_audit_event = local_audit
    audit.persist_audit_event = no_database_audit
    trace_path = Path(os.environ["BENCHMARK_LLM_TRACE_PATH"])
    original = LLMClient.generate_structured

    async def traced(self: LLMClient, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        succeeded = False
        try:
            result = await original(self, *args, **kwargs)
            succeeded = True
            return result
        finally:
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "event_type": "llm.call",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "success": succeeded,
                }) + "\n")

    LLMClient.generate_structured = traced
    from orchestrator.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0


def main() -> int:
    """Run isolated backend measurements or a temporary benchmark server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("econest_exports/benchmarks"))
    args = parser.parse_args()
    if args.serve:
        return _serve(args.port)
    if not 1 <= args.samples <= 5:
        parser.error("--samples must be between 1 and 5")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _unload(BACKENDS["gpu"], args.model)
        cpu = _run_backend("cpu", args.model, args.samples, args.output_dir, args.port)
        gpu = _run_backend("gpu", args.model, args.samples, args.output_dir, args.port)
    finally:
        # Restore the live GPU runner to a warm state after temporary testing.
        _prewarm(BACKENDS["gpu"], args.model)
    report = {
        "kind": "econest_cpu_gpu_workflow",
        "captured_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "runtime_versions": {
            name: str(_request(url, "/api/version").get("version") or "unknown")
            for name, url in BACKENDS.items()
        },
        "prompt_type": "advisory_energy_recommendation",
        "prompt": PROMPT,
        "method": "Temporary loopback API per backend; background services and device actions disabled; model prewarmed before each request; browser-like 1-second polling.",
        "scope": "HTTP advisory routing, MCP energy evidence retrieval, Gemma recommendation generation, and result polling. Excludes browser JavaScript rendering and paint.",
        "backends": {"cpu": cpu, "gpu": gpu},
    }
    path = args.output_dir / f"econest-workflow-cpu-gpu-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved workflow comparison: {path}")
    for name, backend in report["backends"].items():
        print(
            f"{name.upper()}: interpret {backend['median_interpret_ms']} ms; "
            f"MCP data {backend['median_mcp_resource_ms'] + backend['median_mcp_tool_ms']:.1f} ms; "
            f"to result {backend['median_total_to_result_ms']} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
