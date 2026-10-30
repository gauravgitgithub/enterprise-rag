"""Kill one dependency at a time under load and check the API fails safe and recovers.

    uv run python scripts/chaos.py <tenant-id> [target ...]

Runs its own port-forward to svc/api (stop any other one on port 8000 first).

Pass criteria per kill: no 500s (an outage is a 503 or a refused connection, never a crash),
and searches succeed again within RECOVERY_LIMIT_S of the pod being deleted.
"""

import asyncio
import subprocess
import sys
import time
import uuid

from load_test import Result, report, run_load

# (label, pod selector). One API pod locally, so killing it is a full outage until it restarts.
TARGETS = [
    ("reranker", "app=reranker"),
    ("embedder", "app=tei"),
    ("postgres", "app=postgres"),
    ("api", "app=api"),
]
CONCURRENCY = 3
WARMUP_S = 60
# TEI reloads a ~2 GB model from its volume and Postgres replays WAL; both take a while.
RECOVERY_LIMIT_S = 300


async def forward_api_forever() -> None:
    """kubectl port-forward binds to one pod and exits when it dies; restart it until cancelled."""
    while True:
        proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "-n",
            "rag",
            "port-forward",
            "svc/api",
            "8000:80",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await proc.wait()
        finally:
            if proc.returncode is None:
                proc.kill()
        await asyncio.sleep(2)


def kill(selector: str) -> None:
    subprocess.run(
        ["kubectl", "-n", "rag", "delete", "pod", "-l", selector, "--wait=false"],
        check=True,
        capture_output=True,
    )


async def scenario(tenant: uuid.UUID, label: str, selector: str) -> bool:
    seconds = WARMUP_S + RECOVERY_LIMIT_S
    load = asyncio.create_task(run_load(tenant, CONCURRENCY, seconds))
    await asyncio.sleep(WARMUP_S)
    kill(selector)
    killed_at = time.monotonic()
    results: list[Result] = await load
    # Load clock starts ~at task creation, so the kill lands at ~WARMUP_S on its timeline.
    after = [r for r in results if r.started >= WARMUP_S]
    statuses = sorted({r.status for r in after})
    first_failure = next((r for r in after if r.status != 200), None)
    recovered = next(
        (
            r
            for r in after
            if r.status == 200 and first_failure and r.started > first_failure.started
        ),
        None,
    )
    crashes = sum(r.status == 500 for r in results)
    print(f"\n== {label}: killed {time.monotonic() - killed_at:.0f}s ago")
    report(results, seconds)
    if first_failure is None:
        print("  no failed request after the kill (requests rode it out)")
    elif recovered:
        print(
            f"  recovered {recovered.started - WARMUP_S:.0f}s after the kill; statuses {statuses}"
        )
    else:
        print(f"  NOT recovered within {RECOVERY_LIMIT_S}s; statuses {statuses}")
    passed = crashes == 0 and (first_failure is None or recovered is not None)
    print(f"  {'PASS' if passed else 'FAIL'} (500s: {crashes})")
    return passed


async def main(tenant: uuid.UUID, only: list[str]) -> None:
    forward = asyncio.create_task(forward_api_forever())
    await asyncio.sleep(3)
    targets = [t for t in TARGETS if not only or t[0] in only]
    passed = [await scenario(tenant, label, selector) for label, selector in targets]
    forward.cancel()
    sys.exit(0 if all(passed) else 1)


asyncio.run(main(uuid.UUID(sys.argv[1]), sys.argv[2:]))
