import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_rag.api.auth import AuthError
from enterprise_rag.constants.api import QUERY_MAX_CHARS, SEARCH_TOP_N_MAX
from enterprise_rag.constants.retrieval import DEFAULT_SEARCH_TOP_N
from enterprise_rag.context import RequestContext
from enterprise_rag.ingestion.pipeline import Embed
from enterprise_rag.repositories.uow import UnitOfWork


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


def make_app(
    session_factory: async_sessionmaker[AsyncSession],
    embed: Embed,
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

    @app.post("/v1/search")
    async def search(
        body: SearchRequest, ctx: Annotated[RequestContext, Depends(caller)]
    ) -> SearchResponse:
        [query_embedding] = await embed([body.query])
        async with UnitOfWork(session_factory, ctx) as uow:
            hits = await uow.chunks.hybrid_search(body.query, query_embedding, top_n=body.top_n)
            return SearchResponse(
                hits=[
                    Hit(
                        chunk_id=h.chunk.id,
                        document_id=h.chunk.document_id,
                        content=h.chunk.content,
                        section_path=h.chunk.section_path,
                        page=h.chunk.page,
                        score=h.score,
                    )
                    for h in hits
                ]
            )

    return app
