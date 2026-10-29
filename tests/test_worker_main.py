import importlib

import pytest

from enterprise_rag.constants.queues import INGEST_QUEUE, INGEST_TASK
from enterprise_rag.worker.ingest import IngestDeps

ENV = {
    "RAG_DATABASE_URL": "postgresql+asyncpg://rag_app:x@localhost:1/rag",
    "RAG_DATABASE_ADMIN_URL": "postgresql+asyncpg://postgres:x@localhost:1/rag",
    "RAG_AWS_REGION": "eu-west-1",
    "RAG_INGEST_QUEUE_URL": "http://sqs.local/000000000000/ingest",
    "RAG_S3_BUCKET": "documents",
    "RAG_TEI_URL": "http://tei.local",
}


# Break caught: the deployable worker module failing at import or at its first task because
# settings and adapters are wired wrong; unit tests build deps by hand and would not notice.
def test_worker_module_wires_the_ingest_task_from_settings(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    main = importlib.import_module("enterprise_rag.worker.main")
    deps = main.deps()

    assert INGEST_TASK in main.app.tasks
    queues = main.app.conf.broker_transport_options["predefined_queues"]
    assert queues[INGEST_QUEUE]["url"] == ENV["RAG_INGEST_QUEUE_URL"]
    assert isinstance(deps, IngestDeps)
    assert main.deps() is deps  # built once per process: models are heavy


def test_worker_settings_require_the_queue_url(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAG_INGEST_QUEUE_URL")
    from enterprise_rag.config import WorkerSettings

    with pytest.raises(ValueError):
        WorkerSettings(_env_file=None)


# Break caught: the worker pod needing the schema owner's credentials just to start.
def test_worker_settings_do_not_need_admin_credentials(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("RAG_DATABASE_ADMIN_URL")
    from enterprise_rag.config import WorkerSettings

    assert not hasattr(WorkerSettings(_env_file=None), "database_admin_url")
