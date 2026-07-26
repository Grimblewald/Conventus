"""add dynamic_roles to committee_member

Revision ID: 40f8684e20c4
Revises: ab171d308501
Create Date: 2026-06-06 15:41:11.921955

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = '40f8684e20c4'
down_revision = 'ab171d308501'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('committee_members',
                sa.Column('dynamic_roles', sa.JSON(), nullable=True))


def downgrade():
    drop_columns('committee_members', 'dynamic_roles')
