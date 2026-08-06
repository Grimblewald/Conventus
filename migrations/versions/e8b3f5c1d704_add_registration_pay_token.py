"""add a capability token for the durable registration pay link

Revision ID: e8b3f5c1d704
Revises: d4a71c3b9e52
Create Date: 2026-08-06 14:00:00.000000

Nullable and unbackfilled on purpose: tokens are minted lazily on first use
(Registration.ensure_pay_token), so an existing registration gets one the first
time its pay link is needed rather than in a migration.

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns, indexes


# revision identifiers, used by Alembic.
revision = 'e8b3f5c1d704'
down_revision = 'd4a71c3b9e52'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('registrations',
                sa.Column('pay_token', sa.String(length=64), nullable=True))
    if 'ix_registrations_pay_token' not in indexes('registrations'):
        op.create_index('ix_registrations_pay_token', 'registrations',
                        ['pay_token'], unique=True)


def downgrade():
    if 'ix_registrations_pay_token' in indexes('registrations'):
        op.drop_index('ix_registrations_pay_token', table_name='registrations')
    drop_columns('registrations', 'pay_token')
