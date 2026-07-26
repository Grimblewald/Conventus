"""add presenting_author_index and references to abstract

Revision ID: 80b3d7969502
Revises: 1b5ead423541
Create Date: 2026-06-29 23:42:01.347499

Idempotent: the baseline builds the schema from the current models, so these
columns may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


# revision identifiers, used by Alembic.
revision = '80b3d7969502'
down_revision = '1b5ead423541'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('abstracts',
                sa.Column('presenting_author_index', sa.Integer(), nullable=True),
                sa.Column('references', sa.JSON(), nullable=True))


def downgrade():
    drop_columns('abstracts', 'references', 'presenting_author_index')
