"""Benchmark Ollama model latency."""

import asyncio
import csv
import time
from pathlib import Path

from orchestrator.llm.client import LLMClient


PROMPT = """
Analyze the following smart home event:

Motion detected in garage at 2:13 AM.
No occupants expected.
Garage door opened 3 minutes later.
Living room lights remained off.

Provide a short reasoning summary.
"""

MODELS = ["gemma4", "mistral"]

RUNS_PER_MODEL = 3

OUTPUT_FILE = Path("orchestrator/benchmarks/results_llm.csv")


async def benchmark_model(model_name: str) -> list[dict]:
    """Benchmark a single Ollama model."""
    client = LLMClient(model=model_name)

    results = []

    for run in range(RUNS_PER_MODEL):
        start = time.perf_counter()

        try:
            response = await client.generate(
                PROMPT,
                temperature=0.2,
            )

            elapsed = time.perf_counter() - start

            results.append(
                {
                    "model": model_name,
                    "run": run + 1,
                    "latency_seconds": round(elapsed, 3),
                    "response_chars": len(response),
                    "status": "success",
                }
            )

            print(
                f"{model_name} | run {run+1} | "
                f"{elapsed:.3f}s | chars={len(response)}"
            )

        except Exception as exc:
            print(f"{model_name} | FAILED | {exc}")

            results.append(
                {
                    "model": model_name,
                    "run": run + 1,
                    "latency_seconds": -1,
                    "response_chars": 0,
                    "status": "failed",
                }
            )

    await client.close()

    return results


async def main():
    """Run benchmarks for all configured models."""
    all_results = []

    for model in MODELS:
        print(f"\nBenchmarking model: {model}")

        results = await benchmark_model(model)

        all_results.extend(results)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model",
                "run",
                "latency_seconds",
                "response_chars",
                "status",
            ],
        )

        writer.writeheader()

        writer.writerows(all_results)

    print(f"\nSaved results to: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())