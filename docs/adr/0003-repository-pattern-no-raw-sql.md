# ADR 0003: Repository pattern, no raw SQL

Status: accepted

## Decision
All DB access goes through repositories on a `UnitOfWork`. Queries are SQLAlchemy expressions;
`sqlalchemy.text` is banned by ruff (`TID251`) everywhere except `migrations/`.
Repositories stamp `tenant_id` from the verified `RequestContext`, never from caller input.

## Consequences
- Callers never touch a `Session`; one UoW is one transaction (commit on exit, rollback on error).
- Migrations may use raw DDL (policies, roles), which SQLAlchemy cannot express.
