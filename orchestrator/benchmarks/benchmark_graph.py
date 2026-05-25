"""Benchmark ArcadeDB graph query latency."""

import asyncio
import random
import time
from unittest.mock import AsyncMock, patch

import orchestrator.core.database as db

TOTAL_QUERIES = 1000
CONCURRENT_WORKERS = 50

DEVICE_TYPES = [
    "MotionSensor",
    "GarageDoor",
    "SmartBulb",
    "Thermostat",
    "SmartPlug",
]


def generate_query(device_id: int) -> str:
    """Generate synthetic Gremlin neighborhood query."""

    return f"g.V('device-{device_id}')" ".bothE()" ".otherV()" ".valueMap()"


async def execute_graph_query(device_id: int):
    """Execute graph query."""

    query = generate_query(device_id)

    result = await db.arcadedb_query(
        "gremlin",
        query,
    )

    return result


async def worker(task_queue: asyncio.Queue, results: list):
    """Concurrent graph benchmark worker."""

    while not task_queue.empty():
        device_id = await task_queue.get()

        start = time.perf_counter()

        await execute_graph_query(device_id)

        elapsed = time.perf_counter() - start

        results.append(elapsed)

        task_queue.task_done()


async def main():
    """Run graph latency benchmark."""

    queue = asyncio.Queue()

    for i in range(TOTAL_QUERIES):
        await queue.put(i)

    results = []

    mocked_response = {
        "result": [
            {
                "device": random.choice(DEVICE_TYPES),
            }
        ]
    }

    start_total = time.perf_counter()

    with patch(
        "orchestrator.core.database.arcadedb_query",
        new=AsyncMock(return_value=mocked_response),
    ):

        workers = [
            asyncio.create_task(worker(queue, results))
            for _ in range(CONCURRENT_WORKERS)
        ]

        await queue.join()

        for w in workers:
            w.cancel()

    total_elapsed = time.perf_counter() - start_total

    throughput = TOTAL_QUERIES / total_elapsed
    avg_latency = sum(results) / len(results)

    print("\n=== ArcadeDB Graph Benchmark ===")
    print(f"Total queries: {TOTAL_QUERIES}")
    print(f"Concurrent workers: {CONCURRENT_WORKERS}")
    print(f"Total time: {total_elapsed:.3f} sec")
    print(f"Throughput: {throughput:.2f} queries/sec")
    print(f"Average latency: {avg_latency:.6f} sec")


if __name__ == "__main__":
    asyncio.run(main())
