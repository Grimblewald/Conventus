"""add profile_picture to abstract

Revision ID: 2396d1a55dc0
Revises: 099d052323c0
Create Date: 2026-06-06 15:32:48.932617

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = '2396d1a55dc0'
down_revision = '099d052323c0'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('abstracts',
                sa.Column('profile_picture_filename', sa.String(length=240),
                          nullable=True))


def downgrade():
    drop_columns('abstracts', 'profile_picture_filename')
