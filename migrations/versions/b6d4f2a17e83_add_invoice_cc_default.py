"""add per-user default invoice CC

Revision ID: b6d4f2a17e83
Revises: c4e8b19d7a30
Create Date: 2026-08-06 10:00:00.000000

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'b6d4f2a17e83'
down_revision = 'c4e8b19d7a30'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('users',
                sa.Column('invoice_cc_default', sa.String(length=400),
                          nullable=True))


def downgrade():
    drop_columns('users', 'invoice_cc_default')
