# Enterprise RAG v2

[![ci](https://github.com/gauravgitgithub/enterprise-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/gauravgitgithub/enterprise-rag/actions/workflows/ci.yml)

Multi-tenant retrieval-augmented generation for B2B documents: hybrid search, reranking,
grounded answers with citations, PII redaction, and per-tenant + per-group access control
enforced inside Postgres.

## Status

| Slice | Scope | State |
|---|---|---|
| 1 | Data layer: schema, row-level security, repositories, hybrid search | done |
| 2 | Ingestion: S3 upload, outbox, Celery on SQS, parse, redact, chunk, embed | in progress: chunker, idempotent registration, PII redaction, outbox, Celery on SQS publish, ingest pipeline, S3 fetch, TEI embedding, Docling parse, ingest worker, outbox relay process, k8s manifests done |
| 3 | Query API: auth, rerank, OpenAI generation, citations, verifier | done: JWT auth (JWKS), `POST /v1/search` with cross-encoder rerank, `POST /v1/answer` with validated `[n]` citations and a grounding check that withholds unsupported answers |
| 4 | Evals, CI gate, k8s deploy, load and chaos tests | in progress: retrieval eval + CI gate, API and reranker on k8s done; load and chaos tests next |

## Layout

```
src/enterprise_rag/
  constants/      project-wide constants (no env-specific values)
  config.py       settings from env vars / .env
  context.py      RequestContext: tenant + groups from verified JWT claims
  api/            FastAPI query API; JWT (RS256/ES256 via the IdP's JWKS) to RequestContext
  db/             SQLAlchemy models, engine/session factory
  repositories/   all DB access: DocumentRepository, ChunkRepository, UnitOfWork
migrations/       Alembic (async); RLS policies live here
docs/adr/         architecture decision records
tests/            run against a real Postgres 18 + pgvector container
```

## Run locally

Needs Docker (Colima) and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env   # then set passwords
docker run -d --name rag-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=rag -p 5433:5432 pgvector/pgvector:pg18
uv sync
uv run alembic upgrade head
docker exec rag-pg psql -U postgres -d rag -c "ALTER ROLE rag_app PASSWORD '<RAG_DATABASE_URL password>'"
```

Tests start their own container. On Colima, point testcontainers at its socket:

```bash
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock
uv run pytest
```

## Retrieval eval

`evals/` holds a fixed corpus of 23 short policy documents (13 targets, 10 lookalikes that
share vocabulary but state different facts) and 44 questions, each naming the document and
text its answer must come from. The eval ingests the corpus through the real pipeline and
reports recall@5 and MRR with and without the reranker. CI gates on both (`eval` job).

```bash
docker run -d -p 8080:80 -v tei:/data ghcr.io/huggingface/text-embeddings-inference:cpu-1.9 --model-id BAAI/bge-m3
docker run -d -p 8081:80 -v tei:/data ghcr.io/huggingface/text-embeddings-inference:cpu-1.9 --model-id BAAI/bge-reranker-v2-m3
RAG_TEI_URL=http://localhost:8080 RAG_RERANK_URL=http://localhost:8081 uv run pytest -m eval -s
```

## Run on Kubernetes (Colima)

Everything runs in the `rag` namespace: Postgres 18 + pgvector, TEI (bge-m3), a second TEI
(bge-reranker-v2-m3), the query API, the outbox relay and the ingest worker, with ElasticMQ and
SeaweedFS standing in for SQS and S3.

Measured memory: TEI ~3 GiB, reranker ~3.4 GiB, worker ~2.2 GiB peak while parsing, API ~0.45 GiB,
the rest under 0.5 GiB. That is tight in a 10 GiB VM: the local overlay runs one API pod and
overcommits the reranker's request, so a worker parsing a large PDF can push the node into OOM.
Give the VM more memory if the host has it.

```bash
colima start --kubernetes --cpu 6 --memory 10
docker build -t enterprise-rag:dev .
cp deploy/k8s/overlays/local/secrets.env.example deploy/k8s/overlays/local/secrets.env
# replace each CHANGE_ME with: openssl rand -hex 24
cp deploy/k8s/overlays/local/openai.env.example deploy/k8s/overlays/local/openai.env
# set RAG_OPENAI_API_KEY; set your IdP's issuer, audience and JWKS URL in the overlay's kustomization.yaml
kubectl apply -k deploy/k8s/overlays/local
kubectl -n rag get pods -w
```

The API listens on `svc/api` port 80 (`kubectl -n rag port-forward svc/api 8000:80`).
On first start each TEI downloads its model (~2.2 GB each) into its volume. The migrate Job and the relay
retry until Postgres is up. Pods run as non-root with read-only root filesystems, the namespace
enforces the `restricted` Pod Security Standard, and network policies deny all ingress except
the paths the app uses.

## Guarantees tested in slice 1

- A tenant cannot read, search or delete another tenant's documents.
- A session with no tenant context reads zero rows (RLS fails closed).
- Writing a row for another tenant is rejected by Postgres.
- Documents and chunks outside the caller's groups can't be read, even by statements that
  bypass the repositories.
- Writes (insert, update, delete) need every group on the row: one group can't change or
  delete what another group also sees.
- A chunk's ACL always equals its document's, including after the document's ACL changes.
- The same file under a different ACL is a separate document, so uploads never reveal
  hidden documents.
- The application role cannot list tenants.
- Tenant context does not leak across pooled connections.
- Hybrid search ranks an exact-term match above a semantic-only match.

## Development workflow

Test-first: write a failing test, watch it fail for the expected reason, write the minimal code,
refactor while green. Then prove the tests can catch real bugs:

```bash
uv run python scripts/mutation_check.py   # every mutant must be KILLED
```

Each entry in `scripts/mutation_check.py` breaks production code one realistic way and names
the test that must fail. Add an entry with every new behavior.

## Conventions

- No raw SQL outside `migrations/`. Ruff rule `TID251` bans `sqlalchemy.text`.
- Constants go in `src/enterprise_rag/constants/`. Anything that differs per environment
  goes in `.env` (local) or a k8s Secret/ConfigMap (cluster).
