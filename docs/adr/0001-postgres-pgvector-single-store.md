# ADR 0001: Postgres + pgvector as the single retrieval store

Status: accepted

## Context
Retrieval needs dense vectors, keyword search, ACL metadata and tenant isolation in one query.

## Decision
Use Postgres 18 with pgvector (HNSW, cosine) and a generated `tsvector` column for full-text
search. Fuse both with Reciprocal Rank Fusion (k=60) in a single SQL statement.

## Consequences
- One transactional store; ACL and tenant filters apply inside the retrieval query.
- `ts_rank_cd` is not true BM25. Upgrade path: ParadeDB `pg_search` (needs self-managed Postgres).
- Filtered HNSW can return short result sets; mitigated with `hnsw.iterative_scan` and
  `hnsw.ef_search >= candidates` (enforced in Settings).
- Revisit Qdrant if benchmarks show filtered recall dropping or the corpus passes ~10M chunks.
