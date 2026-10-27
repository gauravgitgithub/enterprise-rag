"""acl write checks, acl-scoped dedupe, revoke tenants

Revision ID: 63dffc8afa2e
Revises: 9eb70cb505cc
Create Date: 2026-10-27 21:25:35.435682

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "63dffc8afa2e"
down_revision: str | Sequence[str] | None = "9eb70cb505cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GROUPS = "string_to_array(NULLIF(current_setting('app.groups', true), ''), ',')"
# Reads need overlap (acl_read). Writes need every group on the row: you may only create,
# change or delete what is visible to no one outside your own groups.
WRITE_ALLOWED = f"acl_groups <@ {GROUPS}"
# The subquery runs under RLS too: a document the caller cannot see yields NULL, so it fails.
CHUNK_ACL_MATCHES_DOC = (
    "acl_groups = (SELECT d.acl_groups FROM documents d WHERE d.id = chunks.document_id)"
)
CHECKS = {
    "documents": WRITE_ALLOWED,
    "chunks": f"{WRITE_ALLOWED} AND {CHUNK_ACL_MATCHES_DOC}",
}


def upgrade() -> None:
    """Upgrade schema."""
    for table, check in CHECKS.items():
        op.execute(
            f"CREATE POLICY acl_insert ON {table} AS RESTRICTIVE FOR INSERT WITH CHECK ({check})"
        )
        op.execute(f"DROP POLICY acl_update ON {table}")
        op.execute(
            f"CREATE POLICY acl_update ON {table} AS RESTRICTIVE FOR UPDATE "
            f"USING ({WRITE_ALLOWED}) WITH CHECK ({check})"
        )
        op.execute(f"DROP POLICY acl_delete ON {table}")
        op.execute(
            f"CREATE POLICY acl_delete ON {table} AS RESTRICTIVE FOR DELETE USING ({WRITE_ALLOWED})"
        )

    # Chunk ACLs are a denormalized copy for the search filter; keep them in step with the
    # document in the same statement. SECURITY INVOKER, so the caller's RLS still applies.
    op.execute(
        "CREATE FUNCTION sync_chunk_acl() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path = public AS $$ BEGIN "
        "UPDATE chunks SET acl_groups = NEW.acl_groups WHERE document_id = NEW.id; "
        "RETURN NULL; END $$"
    )
    op.execute(
        "CREATE TRIGGER documents_acl_sync AFTER UPDATE OF acl_groups ON documents "
        "FOR EACH ROW WHEN (OLD.acl_groups IS DISTINCT FROM NEW.acl_groups) "
        "EXECUTE FUNCTION sync_chunk_acl()"
    )

    # Same bytes under a different ACL is a different document: no hidden-row collisions.
    op.drop_constraint("uq_documents_tenant_hash", "documents")
    op.create_unique_constraint(
        "uq_documents_tenant_hash_acl", "documents", ["tenant_id", "content_hash", "acl_groups"]
    )

    # FK checks run as the table owner, so the app role never needs to read tenants.
    op.execute("REVOKE SELECT ON tenants FROM rag_app")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("GRANT SELECT ON tenants TO rag_app")
    op.drop_constraint("uq_documents_tenant_hash_acl", "documents")
    op.create_unique_constraint(
        "uq_documents_tenant_hash", "documents", ["tenant_id", "content_hash"]
    )
    op.execute("DROP TRIGGER documents_acl_sync ON documents")
    op.execute("DROP FUNCTION sync_chunk_acl()")
    for table in CHECKS:
        for name, command in (("acl_update", "UPDATE"), ("acl_delete", "DELETE")):
            op.execute(f"DROP POLICY {name} ON {table}")
            op.execute(
                f"CREATE POLICY {name} ON {table} AS RESTRICTIVE FOR {command} "
                f"USING (acl_groups && {GROUPS})"
            )
        op.execute(f"DROP POLICY acl_insert ON {table}")
