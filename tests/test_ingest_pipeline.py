import hashlib
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import select

from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document
from enterprise_rag.ingestion.chunking import Section
from enterprise_rag.ingestion.pipeline import ingest_document
from enterprise_rag.ingestion.redaction import PiiRedactor
from enterprise_rag.repositories.uow import UnitOfWork
from tests.conftest import onehot

ACL = ["everyone"]
SOURCE = b"Escalate refunds to Priya Sharma.\n\nRefunds are allowed within 30 days."


@pytest.fixture(scope="module")
def redactor() -> PiiRedactor:
    return PiiRedactor()


async def fetch(document: Document) -> bytes:
    return SOURCE


def parse(raw: bytes) -> list[Section]:
    """Paragraph parser: enough structure for the pipeline, no Docling needed."""
    sections, offset = [], 0
    for i, para in enumerate(raw.decode().split("\n\n")):
        sections.append(Section(f"p{i}", para, page=1, offset=offset))
        offset += len(para) + 2
    return sections


class Embedder:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [onehot(i) for i, _ in enumerate(texts)]


async def _new_doc(app_factory, tenant) -> uuid.UUID:
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset(ACL))) as uow:
        doc, _ = await uow.documents.register(
            Document(
                title="Refunds",
                source_uri="s3://t/refunds",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                acl_groups=ACL,
            )
        )
        return doc.id


async def _run(app_factory, tenant, doc_id, redactor, embed) -> int:
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset(ACL))) as uow:
        return await ingest_document(
            uow, doc_id, fetch=fetch, parse=parse, redactor=redactor, embed=embed
        )


# Break caught: raw PII stored or embedded; spans pointing into the unredacted text; the
# document never marked ready.
async def test_ingest_stores_redacted_chunks_with_exact_spans(
    app_factory, admin_engine, make_tenant, redactor
):
    tenant = await make_tenant()
    doc_id = await _new_doc(app_factory, tenant)

    assert await _run(app_factory, tenant, doc_id, redactor, Embedder()) == 2

    async with admin_engine.connect() as conn:
        chunks = (
            await conn.execute(
                select(Chunk.ordinal, Chunk.content, Chunk.char_start, Chunk.char_end)
                .where(Chunk.document_id == doc_id)
                .order_by(Chunk.ordinal)
            )
        ).all()
        status = await conn.scalar(select(Document.status).where(Document.id == doc_id))
    assert [tuple(c) for c in chunks] == [
        (0, "Escalate refunds to <PERSON>.", 0, 29),
        (1, "Refunds are allowed within 30 days.", 31, 66),
    ]
    assert status == "ready"


# Break caught: SQS redelivery (at-least-once) ingesting the same document twice, doubling
# chunks and embedding cost.
async def test_redelivered_job_does_not_ingest_twice(
    app_factory, admin_engine, make_tenant, redactor
):
    tenant = await make_tenant()
    doc_id = await _new_doc(app_factory, tenant)
    embed = Embedder()

    await _run(app_factory, tenant, doc_id, redactor, embed)
    assert await _run(app_factory, tenant, doc_id, redactor, embed) == 0

    assert embed.calls == 1
    async with admin_engine.connect() as conn:
        count = len((await conn.execute(select(Chunk.id).where(Chunk.document_id == doc_id))).all())
    assert count == 2


# Break caught: a job for a document deleted before the worker ran raising forever, so SQS
# retries it until the dead-letter queue.
async def test_job_for_a_deleted_document_is_a_no_op(app_factory, make_tenant, redactor):
    tenant = await make_tenant()

    assert await _run(app_factory, tenant, uuid.uuid4(), redactor, Embedder()) == 0
