import hashlib
import uuid
from collections.abc import Sequence

import httpx
import pytest

from enterprise_rag.api.app import make_app
from enterprise_rag.api.auth import verify_token
from enterprise_rag.constants.api import QUERY_MAX_CHARS
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Chunk, Document
from enterprise_rag.repositories.uow import UnitOfWork
from tests.conftest import onehot
from tests.tokens import AUDIENCE, ISSUER, key_for, mint


async def embed(texts: Sequence[str]) -> list[list[float]]:
    return [onehot(0) for _ in texts]


# Stands in for the cross-encoder: a text's score is the number after "score:" in it, else 0.
async def rerank(query: str, texts: Sequence[str]) -> list[float]:
    return [float(t.split("score:")[1]) if "score:" in t else 0.0 for t in texts]


async def no_generate(question: str, sources: Sequence[str]) -> str:
    raise AssertionError("search must not call the model")


def verify(token: str) -> RequestContext:
    return verify_token(token, key_for, issuer=ISSUER, audience=AUDIENCE)


@pytest.fixture
def client(app_factory):
    app = make_app(app_factory, embed, rerank, no_generate, no_generate, verify)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def _seed(
    app_factory, tenant: uuid.UUID, acl: list[str], text: str, embedding: int = 0
) -> uuid.UUID:
    async with UnitOfWork(app_factory, RequestContext(tenant, frozenset(acl))) as uow:
        doc, _ = await uow.documents.register(
            Document(
                title="Handbook",
                source_uri=f"s3://b/{uuid.uuid4()}",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                acl_groups=acl,
            )
        )
        chunk = Chunk(
            ordinal=0,
            content=text,
            char_start=0,
            char_end=len(text),
            page=3,
            embedding=onehot(embedding),
        )
        await uow.chunks.add_many(doc, [chunk])
        return chunk.id


def _auth(tenant: uuid.UUID, groups: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(tenant, groups)}"}


# Break caught: the search route reachable without a valid token.
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer junk"}, {"Authorization": "Basic dXNlcjpwdw=="}]
)
async def test_search_without_a_valid_token_is_401(client, headers):
    response = await client.post("/v1/search", json={"query": "refunds"}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


# Break caught: search running under a fixed or body-supplied tenant/groups instead of the
# token's, so a caller sees another tenant's or another team's documents.
async def test_search_returns_only_what_the_callers_tenant_and_groups_can_see(
    client, app_factory, make_tenant
):
    tenant, other_tenant = await make_tenant(), await make_tenant()
    mine = await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days.")
    await _seed(app_factory, tenant, ["finance"], "Refunds ledger, finance only.")
    await _seed(app_factory, other_tenant, ["hr"], "Refunds at the other tenant.")

    response = await client.post(
        "/v1/search", json={"query": "refunds", "top_n": 5}, headers=_auth(tenant, ["hr"])
    )

    assert response.status_code == 200
    hits = response.json()["hits"]
    assert [h["chunk_id"] for h in hits] == [str(mine)]
    assert hits[0]["content"] == "Refunds within 30 days."
    assert hits[0]["page"] == 3


# Break caught: the reranker's scores ignored (results in hybrid-search order), top_n not
# applied after reranking, or the fused RRF score reported instead of the reranker's.
async def test_search_orders_by_reranker_score_and_keeps_top_n(client, app_factory, make_tenant):
    tenant = await make_tenant()
    # Hybrid search ranks this one first: exact vector match and three keyword hits.
    await _seed(app_factory, tenant, ["hr"], "Refunds refunds refunds. score:1")
    best = await _seed(app_factory, tenant, ["hr"], "Refunds need a receipt. score:9", embedding=1)
    await _seed(app_factory, tenant, ["hr"], "Unrelated travel policy.", embedding=2)

    response = await client.post(
        "/v1/search", json={"query": "refunds", "top_n": 1}, headers=_auth(tenant, ["hr"])
    )

    assert response.status_code == 200
    hits = response.json()["hits"]
    assert [(h["chunk_id"], h["score"]) for h in hits] == [(str(best), 9.0)]


# Break caught: unbounded queries or top_n reaching the embedder and the database.
@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "x" * (QUERY_MAX_CHARS + 1)},
        {"query": "q", "top_n": 0},
        {"query": "q", "top_n": 1000},
    ],
)
async def test_out_of_range_requests_are_422(client, make_tenant, body):
    response = await client.post(
        "/v1/search", json=body, headers=_auth(await make_tenant(), ["hr"])
    )
    assert response.status_code == 422
