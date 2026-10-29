from collections.abc import Callable

from celery import Celery

from enterprise_rag.worker.ingest import IngestDeps


def register_tasks(app: Celery, deps: Callable[[], IngestDeps]) -> None:
    pass
