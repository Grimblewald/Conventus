"""sub-events can be tentative; their prices are stored in cents

Revision ID: c4d7e2a9f15b
Revises: 9b3e61d0c7a4
Create Date: 2026-10-25 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so the
column may already exist. See app/migration_guards.

Sub-event prices were entered and stored in whole dollars while every other
price is in cents; they become cents. And no sub-event has ever been billed —
attendance was recorded but never charged — so every existing one starts
tentative, which keeps it unbilled until an admin starts billing it.

Both data changes run only when this migration adds the column. A database
that already has it has either run them or was built from the current models,
and repeating the conversion would multiply every price by a hundred again.
"""
import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_columns, columns, drop_columns, has_table


revision = 'c4d7e2a9f15b'
down_revision = '9b3e61d0c7a4'
branch_labels = None
depends_on = None


def upgrade():
    if not has_table('sub_events') or 'tentative' in columns('sub_events'):
        return
    add_columns('sub_events',
                sa.Column('tentative', sa.Boolean(), nullable=False,
                          server_default=sa.false()))
    op.get_bind().execute(
        sa.text("UPDATE sub_events SET price = price * 100, tentative = :yes"),
        {"yes": True})


def downgrade():
    if 'tentative' not in columns('sub_events'):
        return
    op.execute("UPDATE sub_events SET price = price / 100")
    drop_columns('sub_events', 'tentative')
