"""add is_contactable to committee_member

Revision ID: 9676ce7e8ae8
Revises: 1355ee08665c
Create Date: 2026-06-06 15:27:09.473257

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = '9676ce7e8ae8'
down_revision = '1355ee08665c'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('committee_members',
                sa.Column('is_contactable', sa.Boolean(), nullable=False,
                          server_default=sa.text('0')))


def downgrade():
    drop_columns('committee_members', 'is_contactable')
