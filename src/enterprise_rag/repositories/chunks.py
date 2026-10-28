from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Float, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_rag.constants.retrieval import (
    DEFAULT_RRF_K,
    DEFAULT_SEARCH_CANDIDATES,
    DEFAULT_SEARCH_TOP_N,
    FTS_LANGUAGE,
)
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: Chunk
    score: float
    dense_rank: int | None
    sparse_rank: int | None


class ChunkRepository:
    def __init__(
        self, session: AsyncSession, ctx: RequestContext, *, rrf_k: int = DEFAULT_RRF_K
    ) -> None:
        self._session = session
        self._ctx = ctx
        self._rrf_k = rrf_k

    async def add_many(self, document: Document, chunks: Sequence[Chunk]) -> None:
        for chunk in chunks:
            chunk.tenant_id = self._ctx.tenant_id
            chunk.document_id = document.id
            chunk.acl_groups = list(document.acl_groups)
        self._session.add_all(chunks)
        await self._session.flush()

    async def delete_for_document(self, document: Document) -> int:
        result = await self._session.execute(delete(Chunk).where(Chunk.document_id == document.id))
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    def _visible(self) -> list:  # type: ignore[type-arg]
        # Same predicates as the RLS policies. RLS is the backstop; stating them here
        # lets the planner use the tenant/ACL indexes.
        return [
            Chunk.tenant_id == self._ctx.tenant_id,
            Chunk.acl_groups.overlap(sorted(self._ctx.groups)),
        ]

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: Sequence[float],
        *,
        top_n: int = DEFAULT_SEARCH_TOP_N,
        candidates: int = DEFAULT_SEARCH_CANDIDATES,
    ) -> list[SearchHit]:
        """Dense (cosine) + full-text candidates, fused with Reciprocal Rank Fusion."""
        distance = Chunk.embedding.cosine_distance(query_embedding)
        dense_raw = (
            select(Chunk.id, distance.label("dist"))
            .where(*self._visible())
            .order_by(distance)
            .limit(candidates)
            .cte("dense_raw")
        )
        # Re-rank after the LIMIT: with iterative HNSW scans, raw order is only approximate.
        dense = select(
            dense_raw.c.id,
            func.row_number().over(order_by=dense_raw.c.dist).label("rank"),
        ).cte("dense")

        tsquery = func.websearch_to_tsquery(FTS_LANGUAGE, query_text)
        text_score = func.ts_rank_cd(Chunk.tsv, tsquery)
        sparse_raw = (
            select(Chunk.id, text_score.label("s"))
            .where(*self._visible(), Chunk.tsv.op("@@")(tsquery))
            .order_by(text_score.desc())
            .limit(candidates)
            .cte("sparse_raw")
        )
        sparse = select(
            sparse_raw.c.id,
            func.row_number().over(order_by=sparse_raw.c.s.desc()).label("rank"),
        ).cte("sparse")

        k = self._rrf_k
        rrf = func.coalesce(1.0 / (k + dense.c.rank), 0) + func.coalesce(
            1.0 / (k + sparse.c.rank), 0
        )
        fused = (
            select(
                func.coalesce(dense.c.id, sparse.c.id).label("id"),
                cast(rrf, Float).label("score"),
                dense.c.rank.label("dense_rank"),
                sparse.c.rank.label("sparse_rank"),
            )
            .select_from(dense.join(sparse, dense.c.id == sparse.c.id, full=True))
            .cte("fused")
        )

        rows = await self._session.execute(
            select(Chunk, fused.c.score, fused.c.dense_rank, fused.c.sparse_rank)
            .join(fused, Chunk.id == fused.c.id)
            .order_by(fused.c.score.desc(), Chunk.id)
            .limit(top_n)
        )
        return [SearchHit(chunk=c, score=s, dense_rank=d, sparse_rank=sp) for c, s, d, sp in rows]
