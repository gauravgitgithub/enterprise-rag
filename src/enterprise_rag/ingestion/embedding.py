from collections.abc import Sequence

import httpx

from enterprise_rag.constants.embedding import EMBED_BATCH_SIZE
from enterprise_rag.constants.retrieval import EMBEDDING_DIM
from enterprise_rag.ingestion.pipeline import Embed


def make_tei_embed(client: httpx.AsyncClient, *, batch_size: int = EMBED_BATCH_SIZE) -> Embed:
    """Embed via Hugging Face text-embeddings-inference (TEI) serving bge-m3.

    `client` carries the TEI base URL, timeouts and auth.
    """

    async def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            response = await client.post(
                "/embed",
                # truncate: a chunk over the model's window is cut, not rejected.
                json={
                    "inputs": list(texts[start : start + batch_size]),
                    "normalize": True,
                    "truncate": True,
                },
            )
            response.raise_for_status()
            vectors.extend(response.json())
        if any(len(v) != EMBEDDING_DIM for v in vectors):
            raise ValueError(f"embedding dimension mismatch: expected {EMBEDDING_DIM}")
        return vectors

    return embed
