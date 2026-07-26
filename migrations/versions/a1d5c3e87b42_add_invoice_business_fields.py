"""add invoice business fields

Revision ID: a1d5c3e87b42
Revises: f7c1e58a92d4
Create Date: 2026-07-20 15:00:00.000000

Idempotent, and here the table itself may be absent: `invoice_template` was
later absorbed into document_template (b3f7a9c1e2d4), so a database built by
the baseline's create_all from the current models never had it. add_columns
treats a missing table as a skip. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'a1d5c3e87b42'
down_revision = 'f7c1e58a92d4'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('invoice_template',
                sa.Column('business_number', sa.String(length=40), nullable=True),
                sa.Column('payment_instructions', sa.Text(), nullable=True),
                sa.Column('gst_registered', sa.Boolean(), nullable=False,
                          server_default=sa.false()))


def downgrade():
    drop_columns('invoice_template', 'gst_registered', 'payment_instructions',
                 'business_number')
