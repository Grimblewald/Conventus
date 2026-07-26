"""add max_abstracts_per_user to conference

Revision ID: 099d052323c0
Revises: 9676ce7e8ae8
Create Date: 2026-06-06 15:30:00.768105

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = '099d052323c0'
down_revision = '9676ce7e8ae8'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('conferences',
                sa.Column('max_abstracts_per_user', sa.Integer(), nullable=True))


def downgrade():
    drop_columns('conferences', 'max_abstracts_per_user')
