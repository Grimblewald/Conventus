"""optional abstract author and website url

Abstracts can now be admin-entered without an author account
(user_id nullable) and carry an optional presenter website link.

Revision ID: f3a81c60d2e4
Revises: c8e588590700
Create Date: 2026-07-09 23:30:00.000000

Idempotent: the baseline builds the schema from the current models, so the
column may already exist. The alter_column runs unconditionally — relaxing a
constraint that is already relaxed is a no-op, and doing it unguarded keeps a
half-migrated database (column added, nullability not yet applied) recoverable.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns, has_table


# revision identifiers, used by Alembic.
revision = 'f3a81c60d2e4'
down_revision = 'c8e588590700'
branch_labels = None
depends_on = None


def upgrade():
    if not has_table('abstracts'):
        return
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              existing_type=sa.Integer(),
                              nullable=True)
    add_columns('abstracts',
                sa.Column('website_url', sa.String(length=300),
                          nullable=True, server_default=''))


def downgrade():
    drop_columns('abstracts', 'website_url')
    if not has_table('abstracts'):
        return
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              existing_type=sa.Integer(),
                              nullable=False)
