"""convert stored amounts to minor units

Amounts were previously entered and stored as whole dollars. The payment
integration now treats all stored amounts as minor units (cents), so
existing rows are multiplied by 100. Fresh databases are unaffected —
this runs before any data can be entered.

Revision ID: c81f2a7d54b3
Revises: 9c94e0ede9cc
Create Date: 2026-07-19 19:05:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c81f2a7d54b3'
down_revision = '9c94e0ede9cc'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE price_tiers SET amount = amount * 100 WHERE amount IS NOT NULL")
    op.execute("UPDATE price_tiers SET early_bird_amount = early_bird_amount * 100 WHERE early_bird_amount IS NOT NULL")
    op.execute("UPDATE registrations SET amount = amount * 100 WHERE amount IS NOT NULL")


def downgrade():
    op.execute("UPDATE price_tiers SET amount = amount / 100 WHERE amount IS NOT NULL")
    op.execute("UPDATE price_tiers SET early_bird_amount = early_bird_amount / 100 WHERE early_bird_amount IS NOT NULL")
    op.execute("UPDATE registrations SET amount = amount / 100 WHERE amount IS NOT NULL")
