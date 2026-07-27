"""add price to sponsor tiers

Revision ID: c4e8b19d7a30
Revises: f2a6d31c94b7
Create Date: 2026-07-27 16:40:00.000000

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'c4e8b19d7a30'
down_revision = 'f2a6d31c94b7'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('sponsor_tiers', sa.Column('price', sa.Integer(), nullable=True))


def downgrade():
    drop_columns('sponsor_tiers', 'price')
