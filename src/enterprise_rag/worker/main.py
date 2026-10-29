"""Deployable ingest worker: `celery -A enterprise_rag.worker.main worker -Q ingest`."""

import functools

import boto3
import httpx

from enterprise_rag.config import WorkerSettings
from enterprise_rag.constants.embedding import EMBED_TIMEOUT_S
from enterprise_rag.constants.queues import INGEST_QUEUE
from enterprise_rag.db.session import make_engine, make_session_factory
from enterprise_rag.ingestion.embedding import make_tei_embed
from enterprise_rag.ingestion.parsing import make_docling_parse
from enterprise_rag.ingestion.redaction import PiiRedactor
from enterprise_rag.ingestion.storage import make_s3_fetch
from enterprise_rag.worker.celery_app import make_celery
from enterprise_rag.worker.ingest import IngestDeps
from enterprise_rag.worker.tasks import register_tasks

settings = WorkerSettings()
app = make_celery(region=settings.aws_region, queue_urls={INGEST_QUEUE: settings.ingest_queue_url})


@functools.cache
def deps() -> IngestDeps:
    # Built on the first task, inside the worker process: models are heavy, and the DB pool
    # must not be created before Celery forks.
    return IngestDeps(
        make_session_factory(make_engine(settings.database_url)),
        make_s3_fetch(settings.s3_bucket, boto3.client("s3", region_name=settings.aws_region)),
        make_docling_parse(),
        PiiRedactor(),
        make_tei_embed(httpx.AsyncClient(base_url=settings.tei_url, timeout=EMBED_TIMEOUT_S)),
    )


register_tasks(app, deps)
