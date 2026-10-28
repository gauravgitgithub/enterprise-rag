from enterprise_rag.constants.outbox import TOPIC_DOCUMENT_INGEST
from enterprise_rag.db.models import Document
from enterprise_rag.repositories.uow import UnitOfWork


async def register_document(uow: UnitOfWork, document: Document) -> tuple[Document, bool]:
    """Register an upload and, if it is new, queue it for ingestion in the same transaction."""
    doc, created = await uow.documents.register(document)
    if created:
        # The worker needs the document's groups to see it under RLS.
        await uow.outbox.add(
            TOPIC_DOCUMENT_INGEST,
            {
                "tenant_id": str(doc.tenant_id),
                "document_id": str(doc.id),
                "acl_groups": list(doc.acl_groups),
            },
        )
    return doc, created
