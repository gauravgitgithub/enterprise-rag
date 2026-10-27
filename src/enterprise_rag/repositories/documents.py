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

    async def register(self, document: Document) -> tuple[Document, bool]:
        """Insert unless this tenant has the same content under the same ACL.

        Returns (document, created). ON CONFLICT is atomic, so concurrent uploads of the
        same bytes cannot both insert. ACLs are sorted so group order never creates a copy.
        """
        acl = sorted(set(document.acl_groups))
        values = {
            "tenant_id": self._ctx.tenant_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "content_hash": document.content_hash,
            "acl_groups": acl,
        }
        inserted = await self._session.scalar(
            pg_insert(Document)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_documents_tenant_hash_acl")
            .returning(Document)
        )
        if inserted is not None:
            return inserted, True
        # The INSERT policy already required acl <= caller groups, so the existing row is visible.
        existing = await self._session.scalar(
            select(Document).where(
                Document.content_hash == document.content_hash, Document.acl_groups == acl
            )
        )
        assert existing is not None
        return existing, False

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def delete(self, document_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(Document).where(Document.id == document_id).returning(Document.id)
        )
        return result.scalar_one_or_none() is not None
