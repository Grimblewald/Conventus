"""Add payment_portal_enabled to SiteSettings and payment_sent_at to registrations

Revision ID: a1b2c3d4e5f6
Revises: 4a1b2c3d4e5f
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '4a1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('site_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_portal_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')))

    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_sent_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.drop_column('payment_sent_at')

    with op.batch_alter_table('site_settings', schema=None) as batch_op:
        batch_op.drop_column('payment_portal_enabled')
