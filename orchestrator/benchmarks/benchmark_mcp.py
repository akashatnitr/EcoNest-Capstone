"""Benchmark MCP task throughput."""

import asyncio
import time


TOTAL_TASKS = 1000
CONCURRENT_WORKERS = 50


async def mock_mcp_task(task_id: int) -> dict:
    """Simulate lightweight MCP orchestration work."""

    await asyncio.sleep(0.01)

    return {
        "task_id": task_id,
        "status": "completed",
    }


async def worker(task_queue: asyncio.Queue, results: list):
    """Async worker consuming MCP tasks."""

    while not task_queue.empty():
        task_id = await task_queue.get()

        start = time.perf_counter()

        await mock_mcp_task(task_id)

        elapsed = time.perf_counter() - start

        results.append(elapsed)

        task_queue.task_done()


async def main():
    """Run MCP throughput benchmark."""

    queue = asyncio.Queue()

    for i in range(TOTAL_TASKS):
        await queue.put(i)

    results = []

    start_total = time.perf_counter()

    workers = [
        asyncio.create_task(worker(queue, results))
        for _ in range(CONCURRENT_WORKERS)
    ]

    await queue.join()

    total_elapsed = time.perf_counter() - start_total

    throughput = TOTAL_TASKS / total_elapsed
    avg_latency = sum(results) / len(results)

    print("\n=== MCP Throughput Benchmark ===")
    print(f"Total tasks: {TOTAL_TASKS}")
    print(f"Concurrent workers: {CONCURRENT_WORKERS}")
    print(f"Total time: {total_elapsed:.3f} sec")
    print(f"Throughput: {throughput:.2f} tasks/sec")
    print(f"Average task latency: {avg_latency:.4f} sec")

    for w in workers:
        w.cancel()


if __name__ == "__main__":
    asyncio.run(main())