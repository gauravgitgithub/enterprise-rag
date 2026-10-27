"""Break production code one way at a time; each mutation must make the named test fail."""

import subprocess
import sys
from pathlib import Path

MIGRATION = next(Path("migrations/versions").glob("*_initial_schema.py"))
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
        "document.tenant_id = self._ctx.tenant_id",
        "pass",
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
        '.on_conflict_do_nothing(constraint="uq_documents_tenant_hash")',
        "",
        "test_concurrent_registrations_of_same_content_create_one_document",
    ),
    (
        "content dedupe across tenants",
        MIGRATION,
        'sa.UniqueConstraint("tenant_id", "content_hash", name="uq_documents_tenant_hash")',
        'sa.UniqueConstraint("content_hash", name="uq_documents_tenant_hash")',
        "test_same_content_in_another_tenant_is_a_separate_document",
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
        ).returncode
    finally:
        path.write_text(original)
    print(f"{'KILLED ' if rc else 'SURVIVED'}  {name}  ({test})")
    if rc == 0:
        survivors.append(name)

sys.exit(1 if survivors else 0)
