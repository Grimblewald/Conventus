"""track the amount outstanding on a registration

Revision ID: f1c9a2e6b430
Revises: e8b3f5c1d704
Create Date: 2026-08-07 09:00:00.000000

Defaults to 0 and is not backfilled: the balance is derived from the ledger,
and registrations that predate charge lines seed an opening balance lazily the
first time one is needed (Registration.ensure_charge_baseline). Filling this in
here would have to guess what had already been paid.

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'f1c9a2e6b430'
down_revision = 'e8b3f5c1d704'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('registrations',
                sa.Column('amount_outstanding', sa.Integer(), nullable=False,
                          server_default='0'))


def downgrade():
    drop_columns('registrations', 'amount_outstanding')
