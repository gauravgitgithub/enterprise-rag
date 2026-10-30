import asyncio
import functools
from collections.abc import Callable
from typing import Any

from celery import Celery, Task

from enterprise_rag.constants.queues import INGEST_MAX_RETRIES, INGEST_TASK
from enterprise_rag.worker.ingest import IngestDeps, handle_ingest


@functools.cache
def _loop() -> asyncio.AbstractEventLoop:
    # One loop per worker process, created lazily so it is never inherited across fork.
    # The async DB pool binds to it, so a fresh loop per task would break pooled connections.
    return asyncio.new_event_loop()


def register_tasks(app: Celery, deps: Callable[[], IngestDeps]) -> None:
    @app.task(
        name=INGEST_TASK,
        autoretry_for=(Exception,),
        # Bad input never gets better; retrying it only delays the DLQ.
        dont_autoretry_for=(ValueError,),
        retry_backoff=True,
        max_retries=INGEST_MAX_RETRIES,
        bind=True,
    )
    def ingest(self: Task, **payload: Any) -> str:
        last_attempt = self.request.retries >= INGEST_MAX_RETRIES
        return _loop().run_until_complete(handle_ingest(payload, deps(), last_attempt=last_attempt))
