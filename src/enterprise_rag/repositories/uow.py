from types import TracebackType
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.constants.db import (
    GROUPS_SEPARATOR,
    GROUPS_SETTING,
    HNSW_EF_SEARCH_SETTING,
    HNSW_ITERATIVE_SCAN_MODE,
    HNSW_ITERATIVE_SCAN_SETTING,
    TENANT_SETTING,
)
from enterprise_rag.constants.retrieval import DEFAULT_HNSW_EF_SEARCH, DEFAULT_RRF_K
from enterprise_rag.context import RequestContext
from enterprise_rag.repositories.chunks import ChunkRepository
from enterprise_rag.repositories.documents import DocumentRepository


class UnitOfWork:
    """One transaction, bound to one caller's tenant and groups.

    Commits on clean exit, rolls back on exception. The tenant settings are
    transaction-local (set_config is_local=true), so a pooled connection never
    carries one tenant's context into another request.
    """

    documents: DocumentRepository
    chunks: ChunkRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ctx: RequestContext,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        hnsw_ef_search: int = DEFAULT_HNSW_EF_SEARCH,
    ) -> None:
        self._factory = session_factory
        self._ctx = ctx
        self._rrf_k = rrf_k
        self._ef_search = hnsw_ef_search

    async def __aenter__(self) -> Self:
        self._session = self._factory()
        try:
            await self._setup()
        except BaseException:
            # __aexit__ never runs when __aenter__ raises; close here or the connection leaks.
            await self._session.close()
            raise
        self.documents = DocumentRepository(self._session, self._ctx)
        self.chunks = ChunkRepository(self._session, self._ctx, rrf_k=self._rrf_k)
        return self

    async def _setup(self) -> None:
        await self._session.begin()
        await self._session.execute(
            select(
                func.set_config(TENANT_SETTING, str(self._ctx.tenant_id), True),
                func.set_config(
                    GROUPS_SETTING, GROUPS_SEPARATOR.join(sorted(self._ctx.groups)), True
                ),
                # ef_search must be >= the candidate LIMIT or HNSW returns fewer rows.
                func.set_config(HNSW_EF_SEARCH_SETTING, str(self._ef_search), True),
                # Keep scanning when ACL/tenant filters drop rows (pgvector >= 0.8).
                func.set_config(HNSW_ITERATIVE_SCAN_SETTING, HNSW_ITERATIVE_SCAN_MODE, True),
            )
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()
