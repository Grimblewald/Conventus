"""add decline_reason to review_assignments

Revision ID: c8e588590700
Revises: e5e2069e85b0
Create Date: 2026-07-07 15:26:55.705610

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'c8e588590700'
down_revision = 'e5e2069e85b0'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('review_assignments',
                sa.Column('decline_reason', sa.Text(), nullable=True))


def downgrade():
    drop_columns('review_assignments', 'decline_reason')
