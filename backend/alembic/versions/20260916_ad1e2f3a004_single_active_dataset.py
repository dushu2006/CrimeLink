"""Repair and enforce the single-active-dataset invariant.

Revision ID: ad1e2f3a004
Revises: 9c0d1e2f003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ad1e2f3a004"
down_revision: str | None = "9c0d1e2f003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_datasets_single_active"
PREFERRED_DEMO_ID = "demo-dataset-002"


def _repair_active_rows() -> None:
    """Choose one winner without deleting or modifying dataset-owned data."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "datasets" not in inspector.get_table_names():
        return

    datasets = sa.table(
        "datasets",
        sa.column("id", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("activated_at", sa.DateTime()),
        sa.column("created_at", sa.DateTime()),
    )
    rows = list(
        bind.execute(
            sa.select(
                datasets.c.id,
                datasets.c.is_active,
                datasets.c.activated_at,
                datasets.c.created_at,
            )
        ).mappings()
    )
    if not rows:
        return

    preferred = next((row for row in rows if row["id"] == PREFERRED_DEMO_ID), None)
    active = [row for row in rows if row["is_active"]]
    winner = preferred
    if winner is None and active:
        # Preserve an existing active selection. If legacy corruption left
        # several, resolve it reproducibly instead of depending on row order.
        winner = max(
            active,
            key=lambda row: (
                str(row["activated_at"] or row["created_at"] or ""),
                str(row["created_at"] or ""),
                row["id"],
            ),
        )
    if winner is None:
        return

    # Clear first so creation of the unique index (and reruns on partially
    # upgraded databases) can never encounter two TRUE values.
    bind.execute(sa.update(datasets).values(is_active=False))
    bind.execute(
        sa.update(datasets)
        .where(datasets.c.id == winner["id"])
        .values(is_active=True)
    )


def upgrade() -> None:
    _repair_active_rows()
    inspector = sa.inspect(op.get_bind())
    if INDEX_NAME not in {index["name"] for index in inspector.get_indexes("datasets")}:
        op.create_index(
            INDEX_NAME,
            "datasets",
            ["is_active"],
            unique=True,
            postgresql_where=sa.text("is_active IS TRUE"),
            sqlite_where=sa.text("is_active = 1"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if INDEX_NAME in {index["name"] for index in inspector.get_indexes("datasets")}:
        op.drop_index(INDEX_NAME, table_name="datasets")
