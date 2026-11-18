"""conferences remember which columns go in the attendee CSV

Revision ID: b5e83a1f07c2
Revises: c4d7e2a9f15b
Create Date: 2026-11-18 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so the
column may already exist. See app/migration_guards.
"""
import sqlalchemy as sa

from app.migration_guards import add_columns, columns, drop_columns, has_table


revision = 'b5e83a1f07c2'
down_revision = 'c4d7e2a9f15b'
branch_labels = None
depends_on = None


def upgrade():
    if not has_table('conferences') or 'attendee_export' in columns('conferences'):
        return
    add_columns('conferences', sa.Column('attendee_export', sa.JSON(), nullable=True))


def downgrade():
    drop_columns('conferences', 'attendee_export')
