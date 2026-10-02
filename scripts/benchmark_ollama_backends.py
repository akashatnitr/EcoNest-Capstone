#!/usr/bin/env python3
"""Compare the same local Gemma model on Docker CPU and native Mac GPU.

This is a model-only test. It sends a generic prompt directly to Ollama and
does not contact Home Assistant, the EcoNest command API, or any device.
Cold samples unload the model first; warm samples reuse the loaded model.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROMPT = (
    "In four concise sentences, explain why monitoring household energy use "
    "can help reduce waste. Do not invent prices or describe a specific home."
)


def _request(url: str, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call one local Ollama endpoint and return its JSON object."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{url.rstrip('/')}{route}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError(f"Unexpected response from {url}{route}")
    return value


def _model_digest(url: str, model: str) -> str:
    """Return the installed model digest to verify both endpoints match."""
    for item in _request(url, "/api/tags").get("models", []):
        if isinstance(item, dict) and item.get("name") == model:
            return str(item.get("digest", ""))
    raise ValueError(f"{model} is not installed at {url}")


def _sample(url: str, model: str) -> dict[str, Any]:
    """Run one deterministic, non-streaming model request."""
    started = time.perf_counter()
    result = _request(
        url,
        "/api/generate",
        {
            "model": model,
            "prompt": PROMPT,
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "seed": 42, "num_predict": 96},
        },
    )
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    if result.get("done") is not True or not isinstance(result.get("total_duration"), int):
        raise ValueError("Ollama did not return complete timing data")
    eval_ns = int(result.get("eval_duration") or 0)
    tokens = int(result.get("eval_count") or 0)
    return {
        "wall_ms": wall_ms,
        "total_ms": round(result["total_duration"] / 1_000_000, 1),
        "load_ms": round(int(result.get("load_duration") or 0) / 1_000_000, 1),
        "prompt_ms": round(int(result.get("prompt_eval_duration") or 0) / 1_000_000, 1),
        "generation_ms": round(eval_ns / 1_000_000, 1),
        "input_tokens": int(result.get("prompt_eval_count") or 0),
        "output_tokens": tokens,
        "tokens_per_second": round(tokens * 1_000_000_000 / eval_ns, 1) if eval_ns else None,
        "done_reason": str(result.get("done_reason") or ""),
    }


def _median(samples: list[dict[str, Any]], field: str) -> float | None:
    """Return the median of a numeric sample field."""
    values = [item[field] for item in samples if isinstance(item.get(field), (int, float))]
    return round(statistics.median(values), 1) if values else None


def benchmark_backend(url: str, model: str, warm_samples: int) -> dict[str, Any]:
    """Measure a cold request and several warm requests on one endpoint."""
    version = str(_request(url, "/api/version").get("version") or "unknown")
    digest = _model_digest(url, model)
    _request(url, "/api/generate", {"model": model, "keep_alive": 0})
    cold = _sample(url, model)
    loaded = next(
        (item for item in _request(url, "/api/ps").get("models", [])
         if isinstance(item, dict) and item.get("name") == model),
        {},
    )
    warm = [_sample(url, model) for _ in range(warm_samples)]
    _request(url, "/api/generate", {"model": model, "keep_alive": 0})
    return {
        "url": url,
        "ollama_version": version,
        "model_digest": digest,
        "loaded_size_bytes": loaded.get("size"),
        "loaded_gpu_bytes": loaded.get("size_vram"),
        "cold": cold,
        "warm": warm,
        "warm_median_total_ms": _median(warm, "total_ms"),
        "warm_median_wall_ms": _median(warm, "wall_ms"),
        "warm_median_tokens_per_second": _median(warm, "tokens_per_second"),
    }


def main() -> int:
    """Run a sequential comparison and save a local JSON evidence file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-url", default="http://127.0.0.1:11435")
    parser.add_argument("--gpu-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="gemma3:4b")
    parser.add_argument("--warm-samples", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("econest_exports/benchmarks"))
    args = parser.parse_args()
    if not 1 <= args.warm_samples <= 10:
        parser.error("--warm-samples must be between 1 and 10")

    cpu = benchmark_backend(args.cpu_url, args.model, args.warm_samples)
    gpu = benchmark_backend(args.gpu_url, args.model, args.warm_samples)
    if cpu["model_digest"] != gpu["model_digest"]:
        raise ValueError("CPU and GPU endpoints do not have the same model digest")
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "kind": "ollama_cpu_gpu_comparison",
        "model": args.model,
        "model_digest": cpu["model_digest"],
        "prompt": PROMPT,
        "settings": {"temperature": 0, "seed": 42, "num_predict": 96, "warm_samples": args.warm_samples},
        "scope": "Direct Ollama inference only; excludes EcoNest routing, database retrieval, and UI latency.",
        "backends": {"cpu": cpu, "gpu": gpu},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"ollama-cpu-gpu-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved comparison: {path}")
    for name, backend in report["backends"].items():
        print(
            f"{name.upper()}: cold {backend['cold']['total_ms']} ms; "
            f"warm median {backend['warm_median_total_ms']} ms; "
            f"warm generation {backend['warm_median_tokens_per_second']} tokens/s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
