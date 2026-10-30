"""Retrieval quality on a fixed corpus, with the real models.

Skipped by default. Needs TEI serving bge-m3 and bge-reranker-v2-m3:
    RAG_TEI_URL=... RAG_RERANK_URL=... uv run pytest -m eval -s
"""

import hashlib
import json
import os
import re
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from enterprise_rag.api.app import make_app
from enterprise_rag.api.rerank import Rerank, make_tei_rerank
from enterprise_rag.constants.embedding import EMBED_TIMEOUT_S
from enterprise_rag.constants.retrieval import RERANK_TIMEOUT_S
from enterprise_rag.context import RequestContext
from enterprise_rag.db.models import Document
from enterprise_rag.ingestion.chunking import Section
from enterprise_rag.ingestion.embedding import make_tei_embed
from enterprise_rag.ingestion.pipeline import ingest_document
from enterprise_rag.ingestion.redaction import PiiRedactor
from enterprise_rag.repositories.uow import UnitOfWork
from tests.test_api_search import no_generate, verify
from tests.tokens import mint

pytestmark = pytest.mark.eval

EVALS = Path(__file__).parent.parent / "evals"
K = 5
# Gates for the full pipeline. Measured 2026-10-30: hybrid alone recall 1.000 / MRR 0.943,
# with rerank 1.000 / 0.985. MIN_MRR sits above hybrid-only, so a reranker that stops
# helping fails the gate, and a little under the measured score so one flip doesn't.
MIN_RECALL_AT_K = 0.95
MIN_MRR = 0.96
GROUP = "everyone"


def markdown_sections(text: str) -> list[Section]:
    """Split on "## " headings. Stands in for Docling, which accepts only PDF and DOCX."""
    title = text.splitlines()[0].removeprefix("# ")
    sections = []
    for match in re.finditer(r"^## (.+)\n([\s\S]*?)(?=^## |\Z)", text, re.M):
        sections.append(
            Section(
                heading_path=f"{title} > {match.group(1)}",
                text=match.group(2).strip(),
                page=None,
                offset=match.start(2),
            )
        )
    return sections


async def in_hybrid_order(query: str, texts: Sequence[str]) -> list[float]:
    """Rerank switched off: keeps hybrid-search order, to measure what the reranker adds."""
    return [-float(i) for i in range(len(texts))]


async def _ingest_corpus(app_factory, ctx: RequestContext, embed) -> dict[str, str]:
    """Ingests every corpus file through the real pipeline. Returns document id -> file name."""
    redactor = PiiRedactor()
    names = {}
    for path in sorted((EVALS / "corpus").glob("*.md")):
        raw = path.read_bytes()

        async def fetch(document: Document, raw: bytes = raw) -> bytes:
            return raw

        async with UnitOfWork(app_factory, ctx) as uow:
            doc, _ = await uow.documents.register(
                Document(
                    title=path.stem,
                    source_uri=f"s3://evals/{path.name}",
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    acl_groups=[GROUP],
                )
            )
            await ingest_document(
                uow,
                doc.id,
                fetch=fetch,
                parse=lambda raw, name: markdown_sections(raw.decode()),
                redactor=redactor,
                embed=embed,
            )
        names[str(doc.id)] = path.name
    return names


async def _score(app, tenant, names: dict[str, str], questions: list[dict]) -> dict:
    ranks = []
    misses = []
    started = time.monotonic()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        for q in questions:
            response = await client.post(
                "/v1/search",
                json={"query": q["question"], "top_n": K},
                # Fresh per request: a CPU rerank pass outlasts one token's 5-minute lifetime.
                headers={"Authorization": f"Bearer {mint(tenant, [GROUP])}"},
            )
            response.raise_for_status()
            hits = response.json()["hits"]
            rank = next(
                (
                    i
                    for i, h in enumerate(hits, 1)
                    if names[h["document_id"]] == q["doc"] and q["must_contain"] in h["content"]
                ),
                None,
            )
            ranks.append(rank)
            if rank != 1:
                misses.append((q["question"], rank))
    return {
        "recall": sum(r is not None for r in ranks) / len(ranks),
        "mrr": sum(1 / r for r in ranks if r) / len(ranks),
        "misses": misses,
        "seconds_per_query": (time.monotonic() - started) / len(questions),
    }


async def test_retrieval_quality(app_factory, make_tenant):
    embed = make_tei_embed(
        httpx.AsyncClient(base_url=os.environ["RAG_TEI_URL"], timeout=EMBED_TIMEOUT_S)
    )
    rerank: Rerank = make_tei_rerank(
        httpx.AsyncClient(base_url=os.environ["RAG_RERANK_URL"], timeout=RERANK_TIMEOUT_S)
    )
    tenant = await make_tenant()
    names = await _ingest_corpus(app_factory, RequestContext(tenant, frozenset({GROUP})), embed)
    questions = [json.loads(line) for line in (EVALS / "retrieval.jsonl").read_text().splitlines()]

    results = {}
    for label, reranker in [("hybrid", in_hybrid_order), ("hybrid+rerank", rerank)]:
        app = make_app(app_factory, embed, reranker, no_generate, no_generate, verify)
        results[label] = await _score(app, tenant, names, questions)

    print(f"\n{len(questions)} questions, {len(names)} documents, k={K}")
    for label, r in results.items():
        print(
            f"  {label:<14} recall@{K}={r['recall']:.3f}  MRR={r['mrr']:.3f}"
            f"  {r['seconds_per_query']:.2f} s/query"
        )
        for question, rank in r["misses"]:
            print(f"    rank {rank}: {question}")

    full = results["hybrid+rerank"]
    assert full["recall"] >= MIN_RECALL_AT_K
    assert full["mrr"] >= MIN_MRR
