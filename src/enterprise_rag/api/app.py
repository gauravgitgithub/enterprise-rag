import logging
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.api.auth import AuthError
from enterprise_rag.api.generation import (
    NO_ANSWER,
    CheckGrounding,
    Generate,
    resolve_citations,
)
from enterprise_rag.api.rerank import Rerank
from enterprise_rag.constants.api import ANSWER_SOURCES, QUERY_MAX_CHARS, SEARCH_TOP_N_MAX
from enterprise_rag.constants.retrieval import DEFAULT_SEARCH_TOP_N, RERANK_CANDIDATES
from enterprise_rag.context import RequestContext
from enterprise_rag.ingestion.pipeline import Embed
from enterprise_rag.repositories.uow import UnitOfWork

log = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=QUERY_MAX_CHARS)
    top_n: int = Field(default=DEFAULT_SEARCH_TOP_N, ge=1, le=SEARCH_TOP_N_MAX)


class Hit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    section_path: str
    page: int | None
    score: float


class SearchResponse(BaseModel):
    hits: list[Hit]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=QUERY_MAX_CHARS)


class AnswerResponse(BaseModel):
    # Citations appear as [1], [2], ...; [k] refers to citations[k - 1].
    answer: str
    citations: list[Hit]


def make_app(
    session_factory: async_sessionmaker[AsyncSession],
    embed: Embed,
    rerank: Rerank,
    generate: Generate,
    check_grounding: CheckGrounding,
    verify: Callable[[str], RequestContext],
) -> FastAPI:
    app = FastAPI(title="Enterprise RAG")
    bearer = HTTPBearer(auto_error=False)

    # Sync on purpose: a JWKS cache miss is a blocking HTTP call, so FastAPI runs it in a thread.
    def caller(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> RequestContext:
        try:
            if credentials is None:
                raise AuthError("missing bearer token")
            return verify(credentials.credentials)
        except AuthError:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
            ) from None

    async def retrieve(query: str, top_n: int, ctx: RequestContext) -> list[Hit]:
        [query_embedding] = await embed([query])
        async with UnitOfWork(session_factory, ctx) as uow:
            hits = await uow.chunks.hybrid_search(query, query_embedding, top_n=RERANK_CANDIDATES)
        # Outside the transaction: no DB connection held while the cross-encoder runs.
        scores = await rerank(query, [h.chunk.content for h in hits])
        ranked = sorted(zip(scores, hits, strict=True), key=lambda pair: -pair[0])
        return [
            Hit(
                chunk_id=h.chunk.id,
                document_id=h.chunk.document_id,
                content=h.chunk.content,
                section_path=h.chunk.section_path,
                page=h.chunk.page,
                score=score,
            )
            for score, h in ranked[:top_n]
        ]

    @app.post("/v1/search")
    async def search(
        body: SearchRequest, ctx: Annotated[RequestContext, Depends(caller)]
    ) -> SearchResponse:
        return SearchResponse(hits=await retrieve(body.query, body.top_n, ctx))

    @app.post("/v1/answer")
    async def answer(
        body: AnswerRequest, ctx: Annotated[RequestContext, Depends(caller)]
    ) -> AnswerResponse:
        hits = await retrieve(body.question, ANSWER_SOURCES, ctx)
        if not hits:  # nothing the caller may see matches: no model call, nothing to ground on
            return AnswerResponse(answer=NO_ANSWER, citations=[])
        text = await generate(body.question, [h.content for h in hits])
        try:
            text, cited = resolve_citations(text, len(hits))
        except ValueError:
            log.warning("answer cited a source it was not given; withholding it", exc_info=True)
            return AnswerResponse(answer=NO_ANSWER, citations=[])
        # Uncited means ungrounded, however the model phrased it: covers "I don't know" too.
        if not cited:
            return AnswerResponse(answer=NO_ANSWER, citations=[])
        citations = [hits[i] for i in cited]
        unsupported = await check_grounding(text, [c.content for c in citations])
        if unsupported:
            # Count only: claims can quote document text, which stays out of the logs.
            log.warning("answer has %d unsupported claims; withholding it", len(unsupported))
            return AnswerResponse(answer=NO_ANSWER, citations=[])
        return AnswerResponse(answer=text, citations=citations)

    return app
