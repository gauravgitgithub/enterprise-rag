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
# The subquery runs under RLS too: a document the caller cannot see yields NULL, so it fails.
CHUNK_ACL_MATCHES_DOC = (
    "acl_groups = (SELECT d.acl_groups FROM documents d WHERE d.id = chunks.document_id)"
)
# You may only grant access to groups you belong to.
DOC_ACL_WITHIN_CALLER = f"acl_groups <@ {GROUPS}"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"CREATE POLICY acl_insert ON documents AS RESTRICTIVE FOR INSERT "
        f"WITH CHECK ({DOC_ACL_WITHIN_CALLER})"
    )
    op.execute(
        f"CREATE POLICY acl_insert ON chunks AS RESTRICTIVE FOR INSERT "
        f"WITH CHECK ({CHUNK_ACL_MATCHES_DOC})"
    )
    for table, check in (("documents", DOC_ACL_WITHIN_CALLER), ("chunks", CHUNK_ACL_MATCHES_DOC)):
        op.execute(f"DROP POLICY acl_update ON {table}")
        op.execute(
            f"CREATE POLICY acl_update ON {table} AS RESTRICTIVE FOR UPDATE "
            f"USING (acl_groups && {GROUPS}) WITH CHECK ({check})"
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
    for table in ("documents", "chunks"):
        op.execute(f"DROP POLICY acl_update ON {table}")
        op.execute(
            f"CREATE POLICY acl_update ON {table} AS RESTRICTIVE FOR UPDATE "
            f"USING (acl_groups && {GROUPS})"
        )
        op.execute(f"DROP POLICY acl_insert ON {table}")
