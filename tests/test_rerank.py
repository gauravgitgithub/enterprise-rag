import json

import httpx
import pytest

from enterprise_rag.api.rerank import make_tei_rerank


class FakeTei:
    """Records requests; answers like TEI /rerank: sorted by score, `index` into the batch."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[dict] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"path": request.url.path, **body})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "overloaded"})
        scored = [{"index": i, "score": float(t[-1])} for i, t in enumerate(body["texts"])]
        return httpx.Response(200, json=sorted(scored, key=lambda s: -s["score"]))


def client(fake: FakeTei) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://reranker")


# Break caught: one oversized request (TEI rejects it), scores read in TEI's sorted order
# instead of by index, or a later batch's index not offset, so scores land on the wrong chunks.
async def test_batches_texts_and_maps_scores_back_by_index():
    fake = FakeTei()

    scores = await make_tei_rerank(client(fake), batch_size=2)("q", ["t1", "t3", "t2", "t5", "t4"])

    assert scores == [1.0, 3.0, 2.0, 5.0, 4.0]
    assert fake.requests == [
        {"path": "/rerank", "query": "q", "texts": ["t1", "t3"], "truncate": True},
        {"path": "/rerank", "query": "q", "texts": ["t2", "t5"], "truncate": True},
        {"path": "/rerank", "query": "q", "texts": ["t4"], "truncate": True},
    ]


# Break caught: a reranker outage swallowed, so search returns zero-scored, arbitrary order.
async def test_http_errors_raise():
    with pytest.raises(httpx.HTTPStatusError):
        await make_tei_rerank(client(FakeTei(status=503)))("q", ["t1"])


# Break caught: TEI answers 422 to an empty batch, failing searches that found nothing.
async def test_no_texts_means_no_request():
    fake = FakeTei()

    assert await make_tei_rerank(client(fake))("q", []) == []
    assert fake.requests == []
