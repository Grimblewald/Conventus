"""add queue_stats

Revision ID: e1c47af8b920
Revises: d5b719ac3e08
Create Date: 2026-08-24 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so this
table may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import create_table, drop_table


revision = 'e1c47af8b920'
down_revision = 'd5b719ac3e08'
branch_labels = None
depends_on = None


def upgrade():
    create_table(
        'queue_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('worker', sa.String(length=80), nullable=False),
        sa.Column('hour_start', sa.DateTime(), nullable=False, index=True),
        sa.Column('pending', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('peak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('worker', 'hour_start', name='uq_queue_stat_slot'),
    )


def downgrade():
    drop_table('queue_stats')
