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


def verify(token: str) -> RequestContext:
    return verify_token(token, key_for, issuer=ISSUER, audience=AUDIENCE)


@pytest.fixture
def client(app_factory):
    app = make_app(app_factory, embed, verify)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def _seed(app_factory, tenant: uuid.UUID, acl: list[str], text: str) -> uuid.UUID:
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
            ordinal=0, content=text, char_start=0, char_end=len(text), page=3, embedding=onehot(0)
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
