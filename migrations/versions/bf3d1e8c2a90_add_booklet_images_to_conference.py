"""add booklet header / footer / background images to conference

Revision ID: bf3d1e8c2a90
Revises: ca48aea332ef
Create Date: 2026-06-08 12:00:00.000000

Idempotent: the baseline builds the schema from the current models, so these
columns may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = 'bf3d1e8c2a90'
down_revision = 'ca48aea332ef'
branch_labels = None
depends_on = None

COLUMNS = (
    'booklet_header_filename',
    'booklet_footer_filename',
    'booklet_background_filename',
)


def upgrade():
    add_columns('conferences',
                *(sa.Column(name, sa.String(length=255)) for name in COLUMNS))


def downgrade():
    drop_columns('conferences', *COLUMNS)
