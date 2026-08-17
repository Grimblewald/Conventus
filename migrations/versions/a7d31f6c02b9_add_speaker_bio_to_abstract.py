"""add speaker_bio to abstract

Revision ID: a7d31f6c02b9
Revises: f1c9a2e6b430
Create Date: 2026-08-17 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = 'a7d31f6c02b9'
down_revision = 'f1c9a2e6b430'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('abstracts', sa.Column('speaker_bio', sa.Text(), nullable=True))


def downgrade():
    drop_columns('abstracts', 'speaker_bio')
