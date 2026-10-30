from collections.abc import Awaitable, Callable, Sequence

import httpx

from enterprise_rag.constants.retrieval import RERANK_BATCH_SIZE

# Scores each text's relevance to the query; one score per text, in input order.
Rerank = Callable[[str, Sequence[str]], Awaitable[list[float]]]


def make_tei_rerank(client: httpx.AsyncClient, *, batch_size: int = RERANK_BATCH_SIZE) -> Rerank:
    """Rerank via Hugging Face text-embeddings-inference (TEI) serving bge-reranker-v2-m3.

    `client` carries the reranker's TEI base URL, timeouts and auth.
    """

    async def rerank(query: str, texts: Sequence[str]) -> list[float]:
        scores = [0.0] * len(texts)
        for start in range(0, len(texts), batch_size):
            response = await client.post(
                "/rerank",
                json={
                    "query": query,
                    "texts": list(texts[start : start + batch_size]),
                    "truncate": True,
                },
            )
            response.raise_for_status()
            # TEI answers sorted by score; `index` points back into this batch.
            for item in response.json():
                scores[start + item["index"]] = item["score"]
        return scores

    return rerank
