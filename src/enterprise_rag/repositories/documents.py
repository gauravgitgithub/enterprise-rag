import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document


class DocumentRepository:
    def __init__(self, session: AsyncSession, ctx: RequestContext) -> None:
        self._session = session
        self._ctx = ctx

    async def add(self, document: Document) -> Document:
        # Tenant comes from the verified context, never from the caller's object.
        document.tenant_id = self._ctx.tenant_id
        self._session.add(document)
        await self._session.flush()
        return document

    async def register(self, document: Document) -> tuple[Document, bool]:
        """Insert unless this tenant already has the same content. Returns (document, created).

        ON CONFLICT is atomic: concurrent uploads of the same bytes cannot both insert.
        """
        values = {
            "tenant_id": self._ctx.tenant_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "content_hash": document.content_hash,
            "acl_groups": document.acl_groups,
        }
        inserted = await self._session.scalar(
            pg_insert(Document)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_documents_tenant_hash")
            .returning(Document)
        )
        if inserted is not None:
            return inserted, True
        existing = await self.get_by_hash(document.content_hash)
        assert existing is not None  # conflict means the row exists and RLS lets us see it
        return existing, False

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_by_hash(self, content_hash: str) -> Document | None:
        result = await self._session.execute(
            select(Document).where(Document.content_hash == content_hash)
        )
        return result.scalar_one_or_none()

    async def delete(self, document_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(Document).where(Document.id == document_id).returning(Document.id)
        )
        return result.scalar_one_or_none() is not None
