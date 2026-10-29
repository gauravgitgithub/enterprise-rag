import hashlib
import uuid
from collections.abc import Sequence

import httpx
import pytest
from sqlalchemy import select

from enterprise_rag.constants.queues import INGEST_QUEUE, INGEST_TASK
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document
from enterprise_rag.ingestion.chunking import Section
from enterprise_rag.ingestion.parsing import UnparseableDocument
from enterprise_rag.ingestion.redaction import PiiRedactor
from enterprise_rag.ingestion.service import register_document
from enterprise_rag.repositories.uow import UnitOfWork
from enterprise_rag.worker.celery_app import make_celery
from enterprise_rag.worker.ingest import IngestDeps, handle_ingest
from enterprise_rag.worker.tasks import register_tasks
from tests.conftest import onehot

ACL = ["hr"]


@pytest.fixture(scope="module")
def redactor() -> PiiRedactor:
    return PiiRedactor()


async def fetch(document: Document) -> bytes:
    return b"Refunds within 30 days."


def parse(raw: bytes, filename: str) -> list[Section]:
    return [Section("Policy", raw.decode(), 1, 0)]


def parse_fails(raw: bytes, filename: str) -> list[Section]:
    raise UnparseableDocument("corrupt pdf")


async def embed(texts: Sequence[str]) -> list[list[float]]:
    return [onehot(0) for _ in texts]


async def embed_down(texts: Sequence[str]) -> list[list[float]]:
    raise httpx.ConnectError("TEI unreachable")


def deps(app_factory, redactor, *, parse=parse, embed=embed) -> IngestDeps:
    return IngestDeps(app_factory, fetch, parse, redactor, embed)


async def _payload(app_factory, make_tenant) -> dict:
    tenant = await make_tenant()
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset(ACL))) as uow:
        doc, _ = await register_document(
            uow,
            Document(
                title="Refunds",
                source_uri="s3://b/k.pdf",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                acl_groups=ACL,
            ),
        )
    return {"tenant_id": str(tenant), "document_id": str(doc.id), "acl_groups": ACL}


async def _status(admin_engine, payload) -> str:
    async with admin_engine.connect() as conn:
        return await conn.scalar(
            select(Document.status).where(Document.id == uuid.UUID(payload["document_id"]))
        )


# Break caught: worker running without the document's tenant/groups, so RLS hides the document
# and every job silently does nothing.
async def test_job_ingests_the_document_under_its_own_tenant_and_groups(
    app_factory, admin_engine, make_tenant, redactor
):
    payload = await _payload(app_factory, make_tenant)

    assert await handle_ingest(payload, deps(app_factory, redactor)) == "ready"
    assert await _status(admin_engine, payload) == "ready"


# Break caught: an unparseable file retried forever (or until the DLQ) instead of being marked
# failed once; and a redelivered job for it parsing again.
async def test_unparseable_document_is_marked_failed_and_not_retried(
    app_factory, admin_engine, make_tenant, redactor
):
    payload = await _payload(app_factory, make_tenant)
    broken = deps(app_factory, redactor, parse=parse_fails)

    assert await handle_ingest(payload, broken) == "failed"
    assert await _status(admin_engine, payload) == "failed"
    assert await handle_ingest(payload, deps(app_factory, redactor)) == "skipped"


# Break caught: transient outages marked as permanent failures, losing documents that would
# have ingested fine a minute later.
async def test_transient_errors_propagate_and_leave_the_document_pending(
    app_factory, admin_engine, make_tenant, redactor
):
    payload = await _payload(app_factory, make_tenant)

    with pytest.raises(httpx.ConnectError):
        await handle_ingest(payload, deps(app_factory, redactor, embed=embed_down))
    assert await _status(admin_engine, payload) == "pending"


# Break caught: a malformed queue message (it is input from outside the process) crashing in
# odd places or being processed with a guessed tenant.
@pytest.mark.parametrize(
    "payload",
    [
        {"tenant_id": "not-a-uuid", "document_id": str(uuid.uuid4()), "acl_groups": ACL},
        {"tenant_id": str(uuid.uuid4()), "document_id": str(uuid.uuid4()), "acl_groups": ["a,b"]},
        {"tenant_id": str(uuid.uuid4()), "document_id": str(uuid.uuid4())},
    ],
)
async def test_malformed_messages_raise_value_error(app_factory, redactor, payload):
    with pytest.raises(ValueError):
        await handle_ingest(payload, deps(app_factory, redactor))


# Break caught: retry policy inverted: transient errors dropped, or bad input retried 5 times.
def test_task_retries_transient_errors_but_not_bad_input(app_factory, redactor):
    app = make_celery(region="eu-west-1", queue_urls={INGEST_QUEUE: "http://unused"})
    register_tasks(app, lambda: deps(app_factory, redactor, embed=embed_down))
    task = app.tasks[INGEST_TASK]

    transient = task.apply(
        kwargs={"tenant_id": str(uuid.uuid4()), "document_id": str(uuid.uuid4()),
                "acl_groups": ACL}
    )
    bad_input = task.apply(kwargs={"tenant_id": "nope", "document_id": "nope", "acl_groups": ACL})

    assert transient.state == "RETRY"
    assert bad_input.state == "FAILURE"
