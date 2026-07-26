"""add logo height px

Revision ID: f7c1e58a92d4
Revises: e3b9d40a71c5
Create Date: 2026-07-20 12:00:00.000000

Idempotent: the baseline builds the schema from the current models, so this
column may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = 'f7c1e58a92d4'
down_revision = 'e3b9d40a71c5'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('site_settings',
                sa.Column('logo_height_px', sa.Integer(), nullable=True))


def downgrade():
    drop_columns('site_settings', 'logo_height_px')
