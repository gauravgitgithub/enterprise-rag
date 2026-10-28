import json

import httpx
import pytest

from enterprise_rag.constants.retrieval import EMBEDDING_DIM
from enterprise_rag.ingestion.embedding import make_tei_embed


def vec(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIM


class FakeTei:
    """Records requests; answers like TEI: one vector per input, in order."""

    def __init__(self, status: int = 200, dim: int = EMBEDDING_DIM) -> None:
        self.requests: list[dict] = []
        self.status, self.dim = status, dim

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"path": request.url.path, **body})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "overloaded"})
        return httpx.Response(200, json=[[float(t[-1])] * self.dim for t in body["inputs"]])


def client(fake: FakeTei) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="http://tei")


# Break caught: one giant request (TEI rejects it), vectors returned out of order, or long
# chunks failing the whole document because truncation was off.
async def test_batches_inputs_and_keeps_order():
    fake = FakeTei()

    vectors = await make_tei_embed(client(fake), batch_size=2)(["t1", "t2", "t3", "t4", "t5"])

    assert vectors == [vec(1), vec(2), vec(3), vec(4), vec(5)]
    assert fake.requests == [
        {"path": "/embed", "inputs": ["t1", "t2"], "normalize": True, "truncate": True},
        {"path": "/embed", "inputs": ["t3", "t4"], "normalize": True, "truncate": True},
        {"path": "/embed", "inputs": ["t5"], "normalize": True, "truncate": True},
    ]


# Break caught: a different model deployed behind the URL, silently storing vectors of the
# wrong size (or failing later at insert with an unclear error).
async def test_rejects_vectors_of_the_wrong_dimension():
    with pytest.raises(ValueError, match="dimension"):
        await make_tei_embed(client(FakeTei(dim=768)))(["t1"])


# Break caught: errors swallowed, so a job "succeeds" without embeddings instead of retrying.
async def test_http_errors_raise_so_the_job_retries():
    with pytest.raises(httpx.HTTPStatusError):
        await make_tei_embed(client(FakeTei(status=503)))(["t1"])


# Break caught: TEI answers 422 to an empty batch, failing documents that have no text.
async def test_no_texts_means_no_request():
    fake = FakeTei()

    assert await make_tei_embed(client(fake))([]) == []
    assert fake.requests == []
