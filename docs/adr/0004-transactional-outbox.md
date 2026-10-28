# ADR 0004: Transactional outbox for ingestion jobs

Status: accepted

## Context
SQS cannot join a Postgres transaction. Publishing a job directly after the commit loses the
job if the process dies in between; publishing before the commit creates jobs for rows that
roll back.

## Decision
- `uow.outbox.add(topic, payload)` writes an `outbox` row in the caller's transaction.
- `relay_once` (role `rag_relay`) selects pending rows oldest first with
  `FOR UPDATE SKIP LOCKED`, publishes them, then deletes them in the same transaction.
- `outbox` has row-level security per role: `rag_app` is tenant-bound; `rag_relay` drains all
  tenants through its own policy, so no role needs `BYPASSRLS` (superuser-only to grant).
- `rag_relay` may only SELECT and DELETE, plus UPDATE on `created_at`, which row locking requires.

## Consequences
- At-least-once delivery: a failure mid-batch republishes earlier events. Consumers must be
  idempotent (document registration already is).
- Published rows are deleted, so the table holds only the backlog.
- Several relay replicas can run; SKIP LOCKED keeps them off each other's rows.
