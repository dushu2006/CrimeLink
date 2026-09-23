"""Add the case narrative column used by the current case API.

The original initial-schema revision created ``cases`` before the ORM gained a
required description field.  The later deployment-alignment revision repaired
several other omissions, but did not include this column.  A database stamped
at that revision (including an already deployed database) could therefore
report the current head while the cases endpoint still failed at query time.

This is deliberately a normal migration rather than a bootstrap-only repair:
it makes the schema correct on both fresh databases and databases that already
record the previous head, and it preserves existing case rows.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f006"
down_revision: str | None = "ad1e2f3a004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # A server default lets PostgreSQL add the NOT NULL column without failing
    # on existing demo rows.  It is removed immediately so future writes remain
    # governed by the model, not by a hidden database default.  The guard also
    # makes this safe for an operator who repaired the drift before deploying
    # this revision; the revision is still recorded normally.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("cases")}
    if "description" not in columns:
        op.add_column(
            "cases",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )
        op.alter_column("cases", "description", server_default=None)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("cases")}
    if "description" in columns:
        op.drop_column("cases", "description")
