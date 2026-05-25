"""Benchmark MCP graph tool throughput with mocked ArcadeDB."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

from orchestrator.mcp.tools.graph_tools import (
    GetDeviceNeighborsInput,
    get_device_neighbors_handler,
)

TOTAL_TASKS = 1000
CONCURRENT_WORKERS = 50


async def execute_graph_tool(task_id: int):
    """Execute a real MCP graph tool handler."""

    result = await get_device_neighbors_handler(
        GetDeviceNeighborsInput(device_id=f"device-{task_id}")
    )

    return result


async def worker(task_queue: asyncio.Queue, results: list):
    """Concurrent benchmark worker."""

    while not task_queue.empty():
        task_id = await task_queue.get()

        start = time.perf_counter()

        await execute_graph_tool(task_id)

        elapsed = time.perf_counter() - start

        results.append(elapsed)

        task_queue.task_done()


async def main():
    """Run MCP graph-tool throughput benchmark."""

    queue = asyncio.Queue()

    for i in range(TOTAL_TASKS):
        await queue.put(i)

    results = []

    mocked_response = {
        "result": [
            {"name": "MotionSensor"},
            {"name": "GarageDoor"},
        ]
    }

    start_total = time.perf_counter()

    with patch(
        "orchestrator.mcp.tools.graph_tools.arcadedb_query",
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

    throughput = TOTAL_TASKS / total_elapsed
    avg_latency = sum(results) / len(results)

    print("\n=== MCP Graph Tool Benchmark ===")
    print(f"Total tasks: {TOTAL_TASKS}")
    print(f"Concurrent workers: {CONCURRENT_WORKERS}")
    print(f"Total time: {total_elapsed:.3f} sec")
    print(f"Throughput: {throughput:.2f} tasks/sec")
    print(f"Average latency: {avg_latency:.6f} sec")


if __name__ == "__main__":
    asyncio.run(main())
