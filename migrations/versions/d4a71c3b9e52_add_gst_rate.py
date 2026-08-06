"""add a configurable GST rate to the financial identity

Revision ID: d4a71c3b9e52
Revises: b6d4f2a17e83
Create Date: 2026-08-06 12:00:00.000000

Nullable on purpose: existing rows keep NULL, which reads as the Australian
10% default rather than inventing a rate for a society that never chose one.

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'd4a71c3b9e52'
down_revision = 'b6d4f2a17e83'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('financial_identity',
                sa.Column('gst_rate', sa.Float(), nullable=True))


def downgrade():
    drop_columns('financial_identity', 'gst_rate')
