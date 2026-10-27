# ADR 0002: Tenant and ACL isolation enforced by Postgres row-level security

Status: accepted

## Context
A missing `WHERE tenant_id = ...` in application code would leak data across tenants.

## Decision
- The app connects as `rag_app` (NOBYPASSRLS). Tables use ENABLE + FORCE ROW LEVEL SECURITY.
- `UnitOfWork` sets `app.tenant_id` and `app.groups` with `set_config(..., is_local => true)`
  at the start of every transaction.
- Policy `tenant_isolation` (all commands) on documents and chunks, plus restrictive ACL
  policies on both tables. One rule per direction: reads need the row's ACL to overlap the
  caller's groups; INSERT, UPDATE and DELETE need it to be a subset. You can't create, change
  or delete anything visible to a group you are not in. Explicit write policies matter: a
  bare UPDATE/DELETE reads no columns, so Postgres skips SELECT policies for it.
- A chunk's ACL must equal its document's ACL. Trigger `documents_acl_sync` copies a
  document's new ACL onto its chunks in the same statement, so narrowing a document's
  audience also narrows search results. It runs as the caller, under the same policies.
- Dedupe key is `(tenant_id, content_hash, acl_groups)` with groups sorted. A hidden row never
  collides with an upload, so uploads cannot probe for hidden documents. Cost: one copy of
  the embeddings per distinct ACL set.
- The app role has no SELECT on `tenants`; FK checks run as the table owner.
- Unset settings resolve to NULL, so a forgotten context returns zero rows instead of all rows.
- A composite FK `(document_id, tenant_id)` stops a chunk pointing at another tenant's document.
- Repositories still state the same predicates so the planner can use indexes.

## Consequences
- Settings are transaction-local, so pooled connections never carry a previous tenant.
- An ingestion worker must carry every group of the document it writes chunks for.
- Empty ACLs are rejected by a CHECK constraint (fail closed).
