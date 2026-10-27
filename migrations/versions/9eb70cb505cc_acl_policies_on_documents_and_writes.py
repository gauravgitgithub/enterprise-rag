"""acl policies on documents and writes

Revision ID: 9eb70cb505cc
Revises: 8191e8969002
Create Date: 2026-10-27 20:51:33.411995

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9eb70cb505cc"
down_revision: str | Sequence[str] | None = "8191e8969002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GROUPS = "string_to_array(NULLIF(current_setting('app.groups', true), ''), ',')"

# Restrictive policies are AND-ed with tenant_isolation, so both must pass.
POLICIES = [
    ("acl_read", "documents", "SELECT"),
    ("acl_update", "documents", "UPDATE"),
    ("acl_delete", "documents", "DELETE"),
    ("acl_update", "chunks", "UPDATE"),
    ("acl_delete", "chunks", "DELETE"),
]


def upgrade() -> None:
    """Upgrade schema."""
    for name, table, command in POLICIES:
        op.execute(
            f"CREATE POLICY {name} ON {table} AS RESTRICTIVE FOR {command} "
            f"USING (acl_groups && {GROUPS})"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, _ in POLICIES:
        op.execute(f"DROP POLICY {name} ON {table}")
