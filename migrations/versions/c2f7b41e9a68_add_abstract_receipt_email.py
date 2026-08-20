"""add abstract_receipt_email to conference

Revision ID: c2f7b41e9a68
Revises: b8e4a1c7d035
Create Date: 2026-08-20 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = 'c2f7b41e9a68'
down_revision = 'b8e4a1c7d035'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('conferences',
                sa.Column('abstract_receipt_email', sa.Boolean(),
                          nullable=False, server_default='1'))


def downgrade():
    drop_columns('conferences', 'abstract_receipt_email')
