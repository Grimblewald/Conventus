"""Idempotency helpers for Alembic migrations.

WHY these exist: the baseline revision (4a1b2c3d4e5f) builds the schema with
`db.create_all()` from the *current* models, not from a frozen 2026-05-30
snapshot. So on any database that starts from the baseline — a fresh install,
or a raw-backup restore that `scripts/update.sh` stamps to the baseline — every
column and table a later migration adds is ALREADY there before that migration
runs. Without a guard the chain dies on the first "duplicate column name" and
the install can never reach head.

The guard has to be an up-front existence check. The obvious-looking

    with op.batch_alter_table('t') as batch_op:
        try:
            batch_op.add_column(...)
        except sa.exc.OperationalError:
            pass

does NOT work: batch operations are only recorded inside the block and the DDL
is emitted when the context manager exits, so the except clause never sees the
error. That pattern was in seven migrations and none of them was guarding
anything. `tests/test_migrations.py` runs the whole chain from both starting
states and also fails the build if the anti-pattern reappears.

Downgrades are guarded the same way, so a partially-applied revision can still
be stepped back.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


def _inspector():
    return sa.inspect(op.get_bind())


def tables() -> set[str]:
    return set(_inspector().get_table_names())


def has_table(name: str) -> bool:
    return name in tables()


def columns(table: str) -> set[str]:
    if not has_table(table):
        return set()
    return {c["name"] for c in _inspector().get_columns(table)}


def indexes(table: str) -> set[str]:
    if not has_table(table):
        return set()
    return {i["name"] for i in _inspector().get_indexes(table)}


def add_columns(table: str, *cols: sa.Column) -> None:
    """Add every column that is not already on *table*, in one batch.

    A missing table is also a skip, not an error: a migration may target a
    table that no longer exists in the current models (invoice_template, say),
    so a create_all-built database never had it in the first place.
    """
    if not has_table(table):
        return
    existing = columns(table)
    missing = [c for c in cols if c.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for col in missing:
            batch_op.add_column(col)


def drop_columns(table: str, *names: str) -> None:
    """Drop every named column that is actually present, in one batch."""
    if not has_table(table):
        return
    existing = columns(table)
    present = [n for n in names if n in existing]
    if not present:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)


def create_table(name: str, *args, **kwargs) -> None:
    """op.create_table unless the table already exists."""
    if has_table(name):
        return
    op.create_table(name, *args, **kwargs)


def drop_table(name: str) -> None:
    if has_table(name):
        op.drop_table(name)


def create_index(name: str, table: str, cols, *, unique: bool = False) -> None:
    if not has_table(table) or name in indexes(table):
        return
    op.create_index(name, table, cols, unique=unique)


def drop_index(name: str, table: str) -> None:
    if has_table(table) and name in indexes(table):
        op.drop_index(name, table_name=table)
