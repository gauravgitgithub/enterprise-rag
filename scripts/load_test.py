"""Concurrent authenticated /v1/search against the local stack; prints latency and status counts.

uv run python scripts/load_test.py <tenant-id> [concurrency] [seconds]
"""

import asyncio
import itertools
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from devstack import API_URL, mint

QUESTIONS = [json.loads(line)["question"] for line in Path("evals/retrieval.jsonl").open()]
REQUEST_TIMEOUT_S = 120


@dataclass
class Result:
    started: float  # seconds since the run began
    latency: float
    status: int  # 0: no HTTP response (connection refused, reset, timeout)


async def run_load(tenant: uuid.UUID, concurrency: int, seconds: float) -> list[Result]:
    results: list[Result] = []
    questions = itertools.cycle(QUESTIONS)
    begin = time.monotonic()

    async def user(client: httpx.AsyncClient) -> None:
        while (start := time.monotonic()) - begin < seconds:
            try:
                response = await client.post(
                    "/v1/search",
                    json={"query": next(questions), "top_n": 5},
                    headers={"Authorization": f"Bearer {mint(tenant)}"},
                )
                status = response.status_code
            except httpx.HTTPError:
                status = 0
                await asyncio.sleep(1)  # don't spin on a refused connection
            results.append(Result(start - begin, time.monotonic() - start, status))

    async with httpx.AsyncClient(base_url=API_URL, timeout=REQUEST_TIMEOUT_S) as client:
        await asyncio.gather(*(user(client) for _ in range(concurrency)))
    return results


def report(results: list[Result], seconds: float) -> None:
    ok = sorted(r.latency for r in results if r.status == 200)
    counts: dict[int, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"{len(results)} requests in {seconds:.0f}s ({len(ok) / seconds:.2f} ok/s), {counts}")
    if ok:
        p95 = ok[min(len(ok) - 1, int(len(ok) * 0.95))]
        print(f"  latency p50={statistics.median(ok):.2f}s p95={p95:.2f}s max={ok[-1]:.2f}s")


if __name__ == "__main__":
    tenant = uuid.UUID(sys.argv[1])
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 120
    report(asyncio.run(run_load(tenant, concurrency, seconds)), seconds)
