"""add hero_image_mode to conference

Revision ID: 1355ee08665c
Revises: 4a1b2c3d4e5f
Create Date: 2026-06-06 14:11:54.739648

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. It used to be guarded by a try/except *inside* a
batch_alter_table block, which catches nothing — the DDL is emitted when the
block exits. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = '1355ee08665c'
down_revision = '4a1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('conferences',
                sa.Column('hero_image_mode', sa.String(length=16),
                          nullable=False, server_default='cover'))


def downgrade():
    drop_columns('conferences', 'hero_image_mode')
