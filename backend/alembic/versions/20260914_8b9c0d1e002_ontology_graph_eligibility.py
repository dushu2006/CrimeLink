"""Add explicit display identity separation for canonical entities.

Graph eligibility is enforced in the projection/adapters; this migration keeps
investigator display values separate from immutable canonical identifiers in the
relational canonical store.

Revision ID: 8b9c0d1e002
Revises: 7a8b9c0d001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "8b9c0d1e002"
down_revision = "7a8b9c0d001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dataset canonical tables are created by the application bootstrap in
    # older installations (the preceding industry migration predates them).
    # Keep Alembic upgrades safe on both an old database and a fully migrated
    # one; ``create_all`` will create the complete table with this column when
    # the table is absent.
    bind = op.get_bind()
    if inspect(bind).has_table("dataset_entities"):
        columns = {column["name"] for column in inspect(bind).get_columns("dataset_entities")}
        if "display_name" not in columns:
            op.add_column(
                "dataset_entities",
                sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
            )
            op.execute(
                "UPDATE dataset_entities SET display_name = name "
                "WHERE display_name = '' OR display_name IS NULL"
            )
    if not inspect(bind).has_table("dataset_pseudonyms"):
        op.create_table(
            "dataset_pseudonyms",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("dataset_id", sa.String(36), nullable=False),
            sa.Column("canonical_key", sa.String(240), nullable=False),
            sa.Column("pseudonym", sa.String(96), nullable=False),
            sa.Column("entity_type", sa.String(32), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dataset_id", "canonical_key", name="uq_dataset_pseudonyms_key"),
            sa.UniqueConstraint("dataset_id", "pseudonym", name="uq_dataset_pseudonyms_value"),
        )
        op.create_index("ix_dataset_pseudonyms_dataset_id", "dataset_pseudonyms", ["dataset_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("dataset_entities"):
        columns = {column["name"] for column in inspect(bind).get_columns("dataset_entities")}
        if "display_name" in columns:
            op.drop_column("dataset_entities", "display_name")
