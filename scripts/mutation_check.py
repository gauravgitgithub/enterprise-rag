"""Break production code one way at a time; each mutation must make the named test fail."""

import signal
import subprocess
import sys
from pathlib import Path

MUTANT_TIMEOUT_S = 300
# SIGTERM runs `finally` too, so a killed run never leaves a mutant in the source tree.
signal.signal(signal.SIGTERM, signal.default_int_handler)

MIGRATION = next(Path("migrations/versions").glob("*_initial_schema.py"))
ACL_MIGRATION = next(Path("migrations/versions").glob("*_acl_policies_on_documents_and_writes.py"))
WRITE_MIGRATION = next(Path("migrations/versions").glob("*_acl_write_checks_acl_scoped_dedupe_.py"))
DOCS_REPO = Path("src/enterprise_rag/repositories/documents.py")
MUTANTS = [
    (
        "drop ACL policy",
        MIGRATION,
        "AS RESTRICTIVE FOR SELECT",
        "AS PERMISSIVE FOR SELECT",
        "test_rls_hides_acl_restricted_chunks_even_without_repository_filters",
    ),
    (
        "drop tenant policy check",
        MIGRATION,
        "USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})",
        "USING (true) WITH CHECK (true)",
        "test_other_tenant_sees_nothing",
    ),
    (
        "session-wide tenant setting",
        Path("src/enterprise_rag/repositories/uow.py"),
        "func.set_config(TENANT_SETTING, str(self._ctx.tenant_id), True)",
        "func.set_config(TENANT_SETTING, str(self._ctx.tenant_id), False)",
        "test_pooled_connection_does_not_leak_tenant_context",
    ),
    (
        "dense-only fusion",
        Path("src/enterprise_rag/repositories/chunks.py"),
        "+ func.coalesce(\n            1.0 / (k + sparse.c.rank), 0\n        )",
        "",
        "test_hybrid_search_fuses_dense_and_keyword_hits",
    ),
    (
        "comma allowed in groups",
        Path("src/enterprise_rag/constants/auth.py"),
        r"[A-Za-z0-9_.:\-]",
        r"[A-Za-z0-9_.:\-,]",
        "test_request_context_rejects_bad_groups",
    ),
    (
        "caller-supplied tenant on insert",
        Path("src/enterprise_rag/repositories/documents.py"),
        '"tenant_id": self._ctx.tenant_id,',
        '"tenant_id": document.tenant_id,',
        "test_other_tenant_sees_nothing",
    ),
    (
        "chunk offset ignored",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "char_start=section.offset + start",
        "char_start=start",
        "test_short_section_becomes_one_chunk_with_document_offsets",
    ),
    (
        "overlap window off by one",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "words[k][0] >= end - overlap",
        "words[k][0] > end - overlap",
        "test_word_starting_exactly_at_overlap_boundary_is_reused",
    ),
    (
        "chunk cannot fill max exactly",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "words[j + 1][1] - words[i][0] <= max_chars",
        "words[j + 1][1] - words[i][0] < max_chars",
        "test_chunk_may_fill_exactly_max_chars",
    ),
    (
        "no overlap",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "if words[k][0] >= end - overlap",
        "if False",
        "test_chunks_overlap_on_word_boundaries_and_always_add_new_text",
    ),
    (
        "long words not split",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "range(m.start(), m.end(), max_chars)",
        "[m.start()]",
        "test_word_longer_than_max_chars_is_hard_split",
    ),
    (
        "plain insert, no conflict handling",
        Path("src/enterprise_rag/repositories/documents.py"),
        '.on_conflict_do_nothing(constraint="uq_documents_tenant_hash_acl")',
        "",
        "test_concurrent_registrations_of_same_content_create_one_document",
    ),
    (
        "documents readable outside groups",
        ACL_MIGRATION,
        '("acl_read", "documents", "SELECT"),',
        "",
        "test_restricted_document_metadata_is_invisible_outside_its_groups",
    ),
    (
        # Migration 3 recreates acl_delete, so it is the one to mutate.
        "rows deletable outside groups",
        WRITE_MIGRATION,
        "FOR DELETE USING ({WRITE_ALLOWED})",
        "FOR DELETE USING (true)",
        "test_bare_update_and_delete_cannot_touch_rows_outside_groups",
    ),
    (
        "delete gated by overlap, not subset",
        WRITE_MIGRATION,
        "FOR DELETE USING ({WRITE_ALLOWED})",
        "FOR DELETE USING (acl_groups && {GROUPS})",
        "test_cannot_delete_a_document_shared_with_groups_you_are_not_in",
    ),
    (
        "chunk writes skip the subset check",
        WRITE_MIGRATION,
        '"chunks": f"{WRITE_ALLOWED} AND {CHUNK_ACL_MATCHES_DOC}",',
        '"chunks": CHUNK_ACL_MATCHES_DOC,',
        "test_cannot_add_chunks_visible_to_groups_you_are_not_in",
    ),
    (
        "chunk ACLs not synced on document change",
        WRITE_MIGRATION,
        "AFTER UPDATE OF acl_groups ON documents",
        "AFTER UPDATE OF title ON documents",
        "test_narrowing_a_document_acl_hides_its_chunks",
    ),
    (
        # Migration 3 recreates acl_update, so it is the one to mutate.
        "rows updatable outside groups",
        WRITE_MIGRATION,
        'f"USING ({WRITE_ALLOWED}) WITH CHECK ({check})"',
        'f"USING (true) WITH CHECK ({check})"',
        "test_bare_update_and_delete_cannot_touch_rows_outside_groups",
    ),
    (
        "chunk ACL not tied to document",
        WRITE_MIGRATION,
        '"acl_groups = (SELECT d.acl_groups FROM documents d WHERE d.id = chunks.document_id)"',
        '"true"',
        "test_chunk_acl_must_equal_its_document_acl",
    ),
    (
        "chunks attachable to invisible docs",
        WRITE_MIGRATION,
        '"acl_groups = (SELECT d.acl_groups FROM documents d WHERE d.id = chunks.document_id)"',
        '"true"',
        "test_cannot_attach_chunks_to_an_invisible_document",
    ),
    (
        "grant to groups you are not in",
        WRITE_MIGRATION,
        'f"acl_groups <@ {GROUPS}"',
        'f"acl_groups && {GROUPS}"',
        "test_cannot_create_a_document_for_groups_you_are_not_in",
    ),
    (
        "relabel into foreign groups",
        WRITE_MIGRATION,
        'f"acl_groups <@ {GROUPS}"',
        'f"acl_groups && {GROUPS}"',
        "test_cannot_relabel_a_document_into_groups_you_are_not_in",
    ),
    (
        "app role reads tenants",
        WRITE_MIGRATION,
        'op.execute("REVOKE SELECT ON tenants FROM rag_app")',
        "pass",
        "test_app_role_cannot_list_tenants",
    ),
    (
        "dedupe ignores ACL",
        WRITE_MIGRATION,
        '["tenant_id", "content_hash", "acl_groups"]',
        '["tenant_id", "content_hash"]',
        "test_same_content_with_a_different_acl_is_a_separate_document",
    ),
    (
        "dedupe across tenants",
        WRITE_MIGRATION,
        '["tenant_id", "content_hash", "acl_groups"]',
        '["content_hash", "acl_groups"]',
        "test_same_content_in_another_tenant_is_a_separate_document",
    ),
    (
        "ACL order matters",
        DOCS_REPO,
        "acl = sorted(set(document.acl_groups))",
        "acl = list(document.acl_groups)",
        "test_acl_order_does_not_create_a_duplicate",
    ),
    (
        "UoW leaks connection on failed setup",
        Path("src/enterprise_rag/repositories/uow.py"),
        "            await self._session.close()\n            raise",
        "            raise",
        "test_failed_setup_returns_the_connection_to_the_pool",
    ),
    (
        "migrations need the app URL",
        Path("src/enterprise_rag/config.py"),
        "    database_admin_url: str\n",
        "    database_admin_url: str\n    database_url: str\n",
        "test_migrations_need_only_the_admin_url",
    ),
    (
        "ordinals restart per section",
        Path("src/enterprise_rag/ingestion/chunking.py"),
        "ordinal=len(chunks),",
        "ordinal=start,",
        "test_ordinals_and_offsets_continue_across_sections",
    ),
    (
        "redact every entity type",
        Path("src/enterprise_rag/ingestion/redaction.py"),
        "entities=list(REDACTED_ENTITIES)",
        "entities=None",
        "test_places_and_dates_are_kept",
    ),
    (
        "names not redacted",
        Path("src/enterprise_rag/constants/redaction.py"),
        '    "PERSON",\n',
        "",
        "test_person_name_is_replaced",
    ),
    (
        "section offsets not recomputed",
        Path("src/enterprise_rag/ingestion/redaction.py"),
        "offset=offset)",
        "offset=section.offset)",
        "test_redacted_sections_get_offsets_into_the_redacted_document",
    ),
    (
        "separator missing from offsets",
        Path("src/enterprise_rag/ingestion/redaction.py"),
        "offset += len(redacted.text) + len(SECTION_SEPARATOR)",
        "offset += len(redacted.text)",
        "test_redacted_sections_get_offsets_into_the_redacted_document",
    ),
    (
        "relays read without row locks",
        Path("src/enterprise_rag/outbox_relay.py"),
        ".with_for_update(skip_locked=True)",
        "",
        "test_concurrent_relays_publish_each_event_once",
    ),
    (
        "publish failures swallowed",
        Path("src/enterprise_rag/outbox_relay.py"),
        "            await publish(event.topic, event.payload)",
        "            with __import__('contextlib').suppress(Exception):\n"
        "                await publish(event.topic, event.payload)",
        "test_failed_publish_keeps_the_event_for_the_next_run",
    ),
    (
        "published events never removed",
        Path("src/enterprise_rag/outbox_relay.py"),
        "        if events:",
        "        if False:",
        "test_committed_event_is_published_once_and_then_removed",
    ),
    (
        "outbox committed outside the caller's transaction",
        Path("src/enterprise_rag/repositories/outbox.py"),
        "await self._session.flush()",
        "await self._session.commit()",
        "test_rolled_back_transaction_publishes_nothing",
    ),
    (
        "app role drains every tenant",
        next(Path("migrations/versions").glob("*_outbox.py")),
        "ON outbox TO rag_relay USING (true)",
        "ON outbox TO rag_relay, rag_app USING (true)",
        "test_app_role_sees_only_its_own_tenants_events",
    ),
    (
        "duplicates queued again",
        Path("src/enterprise_rag/ingestion/service.py"),
        "    if created:",
        "    if True:",
        "test_new_document_queues_one_ingest_job_and_duplicates_queue_none",
    ),
    (
        "job sent to the wrong queue",
        Path("src/enterprise_rag/worker/celery_app.py"),
        "kwargs=payload, queue=queue)",
        'kwargs=payload, queue="default")',
        "test_relayed_event_reaches_a_celery_worker",
    ),
    (
        "unknown topics silently routed",
        Path("src/enterprise_rag/worker/celery_app.py"),
        "task, queue = TOPIC_ROUTES[topic]",
        "task, queue = TOPIC_ROUTES.get(topic, next(iter(TOPIC_ROUTES.values())))",
        "test_unknown_topic_is_refused_so_the_event_stays_queued",
    ),
    (
        "unredacted text chunked and embedded",
        Path("src/enterprise_rag/ingestion/pipeline.py"),
        "redacted.sections, max_chars=",
        "sections, max_chars=",
        "test_ingest_stores_redacted_chunks_with_exact_spans",
    ),
    (
        "redelivered jobs ingest again",
        Path("src/enterprise_rag/ingestion/pipeline.py"),
        " or document.status == DOCUMENT_STATUS_READY",
        "",
        "test_redelivered_job_does_not_ingest_twice",
    ),
    (
        "missing document crashes the job",
        Path("src/enterprise_rag/ingestion/pipeline.py"),
        "if document is None or document.status",
        "if document.status",
        "test_job_for_a_deleted_document_is_a_no_op",
    ),
    (
        "document never marked ready",
        Path("src/enterprise_rag/ingestion/pipeline.py"),
        "    document.status = DOCUMENT_STATUS_READY",
        "    pass",
        "test_ingest_stores_redacted_chunks_with_exact_spans",
    ),
]

survivors = []
for name, path, old, new, test in MUTANTS:
    original = path.read_text()
    assert old in original, f"{name}: pattern not found in {path}"
    path.write_text(original.replace(old, new, 1))
    try:
        rc = subprocess.run(
            ["uv", "run", "pytest", "-q", "-x", "-k", test, "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=MUTANT_TIMEOUT_S,
        ).returncode
    except subprocess.TimeoutExpired:
        rc = 1  # a hang is a failing test: the mutant is caught
    finally:
        path.write_text(original)
    print(f"{'KILLED ' if rc else 'SURVIVED'}  {name}  ({test})")
    if rc == 0:
        survivors.append(name)

sys.exit(1 if survivors else 0)
