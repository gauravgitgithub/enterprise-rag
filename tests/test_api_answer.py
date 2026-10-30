from collections.abc import Sequence

import httpx
import pytest

from enterprise_rag.api.app import make_app
from enterprise_rag.api.generation import NO_ANSWER
from tests.test_api_search import _auth, _seed, embed, rerank, verify


class FakeModel:
    """Records what the model was asked; replies with a fixed answer."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, question: str, sources: Sequence[str]) -> str:
        self.calls.append((question, list(sources)))
        return self.reply


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def client(app_factory, model):
    app = make_app(app_factory, embed, rerank, model, verify)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# Break caught: the answer route reachable without a valid token.
async def test_answer_without_a_valid_token_is_401(client):
    response = await client.post("/v1/answer", json={"question": "refunds?"})
    assert response.status_code == 401


# Break caught: the model given chunks the caller can't see (another tenant's or group's), or
# given them in hybrid order instead of reranked order; citations not mapped to those chunks.
async def test_answer_is_grounded_in_the_callers_reranked_chunks(
    client, model, app_factory, make_tenant
):
    tenant, other = await make_tenant(), await make_tenant()
    low = await _seed(app_factory, tenant, ["hr"], "Refunds need a form. score:1")
    high = await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days. score:9", 1)
    await _seed(app_factory, tenant, ["finance"], "Refunds ledger. score:5")
    await _seed(app_factory, other, ["hr"], "Refunds elsewhere. score:7")
    model.reply = "Within 30 days [1], with a form [2]."

    response = await client.post(
        "/v1/answer", json={"question": "How do refunds work?"}, headers=_auth(tenant, ["hr"])
    )

    assert response.status_code == 200
    assert model.calls == [
        (
            "How do refunds work?",
            ["Refunds within 30 days. score:9", "Refunds need a form. score:1"],
        )
    ]
    body = response.json()
    assert body["answer"] == "Within 30 days [1], with a form [2]."
    assert [c["chunk_id"] for c in body["citations"]] == [str(high), str(low)]


# Break caught: uncited sources returned as citations, or citations not renumbered to match.
async def test_only_cited_chunks_are_returned_renumbered(client, model, app_factory, make_tenant):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds need a form. score:9")
    second = await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days. score:1", 1)
    model.reply = "Within 30 days [2]."

    response = await client.post(
        "/v1/answer", json={"question": "refunds?"}, headers=_auth(tenant, ["hr"])
    )

    body = response.json()
    assert body["answer"] == "Within 30 days [1]."
    assert [c["chunk_id"] for c in body["citations"]] == [str(second)]


# Break caught: an answer citing a source it was never given shown to the user as grounded.
async def test_answer_citing_a_missing_source_is_withheld(client, model, app_factory, make_tenant):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds need a form.")
    model.reply = "Refunds take 90 days [4]."

    response = await client.post(
        "/v1/answer", json={"question": "refunds?"}, headers=_auth(tenant, ["hr"])
    )

    assert response.json() == {"answer": NO_ANSWER, "citations": []}


# Break caught: the model called with no sources, free to answer from its own knowledge.
async def test_no_visible_chunks_means_no_model_call(client, model, make_tenant):
    response = await client.post(
        "/v1/answer", json={"question": "refunds?"}, headers=_auth(await make_tenant(), ["hr"])
    )

    assert response.json() == {"answer": NO_ANSWER, "citations": []}
    assert model.calls == []
