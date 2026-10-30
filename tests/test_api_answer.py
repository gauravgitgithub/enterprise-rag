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


class FakeJudge:
    """Records what the grounding check was asked; flags the configured claims."""

    def __init__(self) -> None:
        self.unsupported: list[str] = []
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, answer: str, sources: Sequence[str]) -> list[str]:
        self.calls.append((answer, list(sources)))
        return self.unsupported


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def judge():
    return FakeJudge()


@pytest.fixture
def client(app_factory, model, judge):
    app = make_app(app_factory, embed, rerank, model, judge, verify)
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


# Break caught: the grounding check skipped, judging the raw answer instead of the renumbered
# one, or given every retrieved chunk instead of only the cited ones.
async def test_grounding_check_sees_the_renumbered_answer_and_only_cited_chunks(
    client, model, judge, app_factory, make_tenant
):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds need a form. score:9")
    await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days. score:1", 1)
    model.reply = "Within 30 days [2]."

    await client.post("/v1/answer", json={"question": "refunds?"}, headers=_auth(tenant, ["hr"]))

    assert judge.calls == [("Within 30 days [1].", ["Refunds within 30 days. score:1"])]


# Break caught: an answer with claims its sources don't support shown to the user.
async def test_answer_with_unsupported_claims_is_withheld(
    client, model, judge, app_factory, make_tenant
):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days.")
    model.reply = "Within 30 days [1], and you get a bonus [1]."
    judge.unsupported = ["you get a bonus"]

    response = await client.post(
        "/v1/answer", json={"question": "refunds?"}, headers=_auth(tenant, ["hr"])
    )

    assert response.json() == {"answer": NO_ANSWER, "citations": []}


# Break caught: an uncited answer (the model's own knowledge) shown to the user.
async def test_uncited_answer_is_withheld_without_a_grounding_call(
    client, model, judge, app_factory, make_tenant
):
    tenant = await make_tenant()
    await _seed(app_factory, tenant, ["hr"], "Refunds within 30 days.")
    model.reply = "Refunds usually take two weeks."

    response = await client.post(
        "/v1/answer", json={"question": "refunds?"}, headers=_auth(tenant, ["hr"])
    )

    assert response.json() == {"answer": NO_ANSWER, "citations": []}
    assert judge.calls == []
