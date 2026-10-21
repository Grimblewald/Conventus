"""corrected documents point at the one they replace

Revision ID: 9b3e61d0c7a4
Revises: 4ff2950cc305
Create Date: 2026-10-21 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so the
columns may already exist. See app/migration_guards.
"""
import sqlalchemy as sa

from app.migration_guards import (add_columns, create_index, drop_columns,
                                  drop_index)


revision = '9b3e61d0c7a4'
down_revision = '4ff2950cc305'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('issued_documents',
                sa.Column('replaces_id', sa.Integer(), nullable=True),
                sa.Column('reason', sa.Text(), nullable=True))
    create_index('ix_issued_documents_replaces_id', 'issued_documents',
                 ['replaces_id'])


def downgrade():
    drop_index('ix_issued_documents_replaces_id', 'issued_documents')
    drop_columns('issued_documents', 'replaces_id', 'reason')
