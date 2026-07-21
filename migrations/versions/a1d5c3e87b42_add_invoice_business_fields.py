"""add invoice business fields

Revision ID: a1d5c3e87b42
Revises: f7c1e58a92d4
Create Date: 2026-07-20 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1d5c3e87b42'
down_revision = 'f7c1e58a92d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('invoice_template', schema=None) as batch_op:
        batch_op.add_column(sa.Column('business_number', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('payment_instructions', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('gst_registered', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('invoice_template', schema=None) as batch_op:
        batch_op.drop_column('gst_registered')
        batch_op.drop_column('payment_instructions')
        batch_op.drop_column('business_number')
