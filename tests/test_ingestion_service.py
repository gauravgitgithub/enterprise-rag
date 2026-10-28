import hashlib
import uuid

from sqlalchemy import select

from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document, OutboxEvent
from enterprise_rag.ingestion.service import register_document
from enterprise_rag.repositories.uow import UnitOfWork

EVERYONE = frozenset({"everyone"})


def _doc(content_hash: str) -> Document:
    return Document(
        title="Handbook", source_uri="s3://t/h", content_hash=content_hash, acl_groups=["everyone"]
    )


async def _events_for(admin_engine, document_id: uuid.UUID) -> list[tuple[str, dict]]:
    async with admin_engine.connect() as conn:
        rows = await conn.execute(
            select(OutboxEvent.topic, OutboxEvent.payload).where(
                OutboxEvent.payload["document_id"].astext == str(document_id)
            )
        )
        return [tuple(r) for r in rows]


# Break caught: new uploads never queued for ingestion, or duplicates queued again, or the job
# missing the groups the worker needs to see the document under RLS.
async def test_new_document_queues_one_ingest_job_and_duplicates_queue_none(
    app_factory, admin_engine, make_tenant
):
    tenant = await make_tenant()
    content_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    ctx = RequestContext(tenant, EVERYONE)

    async with UnitOfWork(app_factory, ctx) as uow:
        doc, _ = await register_document(uow, _doc(content_hash))
    async with UnitOfWork(app_factory, ctx) as uow:
        await register_document(uow, _doc(content_hash))

    assert await _events_for(admin_engine, doc.id) == [
        (
            "document.ingest",
            {"tenant_id": str(tenant), "document_id": str(doc.id), "acl_groups": ["everyone"]},
        )
    ]
