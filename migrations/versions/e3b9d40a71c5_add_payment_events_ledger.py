"""add payment events ledger

Revision ID: e3b9d40a71c5
Revises: c81f2a7d54b3
Create Date: 2026-07-20 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3b9d40a71c5'
down_revision = 'c81f2a7d54b3'
branch_labels = None
depends_on = None


def upgrade():
    # Idempotency guard: db.create_all() on boot may have already created this
    # table (and its indexes) from the current models, which match this schema
    # exactly — so skip the whole block when it's already present.
    if 'payment_events' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('payment_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('transaction_id', sa.String(length=120), nullable=True),
        sa.Column('merchant_reference', sa.String(length=120), nullable=True),
        sa.Column('registration_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=80), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(length=400), nullable=True),
        sa.ForeignKeyConstraint(['registration_id'], ['registrations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('payment_events', schema=None) as batch_op:
        batch_op.create_index('ix_payment_events_created_at', ['created_at'])
        batch_op.create_index('ix_payment_events_transaction_id', ['transaction_id'])
        batch_op.create_index('ix_payment_events_merchant_reference', ['merchant_reference'])
        batch_op.create_index('ix_payment_events_registration_id', ['registration_id'])


def downgrade():
    op.drop_table('payment_events')
