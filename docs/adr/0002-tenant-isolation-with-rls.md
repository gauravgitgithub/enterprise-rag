# ADR 0002: Tenant and ACL isolation enforced by Postgres row-level security

Status: accepted

## Context
A missing `WHERE tenant_id = ...` in application code would leak data across tenants.

## Decision
- The app connects as `rag_app` (NOBYPASSRLS). Tables use ENABLE + FORCE ROW LEVEL SECURITY.
- `UnitOfWork` sets `app.tenant_id` and `app.groups` with `set_config(..., is_local => true)`
  at the start of every transaction.
- Policy `tenant_isolation` (all commands) and restrictive policy `acl_read` (SELECT on chunks).
- Unset settings resolve to NULL, so a forgotten context returns zero rows instead of all rows.
- A composite FK `(document_id, tenant_id)` stops a chunk pointing at another tenant's document.
- Repositories still state the same predicates so the planner can use indexes.

## Consequences
- Settings are transaction-local, so pooled connections never carry a previous tenant.
- Postgres applies SELECT policies to rows touched by UPDATE/DELETE: an ingestion worker
  must carry the groups of the chunks it rewrites.
- Empty ACLs are rejected by a CHECK constraint (fail closed).
