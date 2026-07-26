"""add registration_id to abstracts

Revision ID: 1da8cf69604f
Revises: 80b3d7969502
Create Date: 2026-07-02 17:44:29.593251

Idempotent: the baseline builds the schema from the current models, so the
column, its index and its foreign key may already exist. The three go in one
batch (adding an FK on SQLite rebuilds the table), so the whole block is
skipped as a unit when the column is already present — a create_all-built
`abstracts` carries all three.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import columns, drop_columns, has_table


# revision identifiers, used by Alembic.
revision = '1da8cf69604f'
down_revision = '80b3d7969502'
branch_labels = None
depends_on = None


def upgrade():
    if 'registration_id' in columns('abstracts'):
        return
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('registration_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_abstracts_registration_id'), ['registration_id'], unique=False)
        batch_op.create_foreign_key('fk_abstracts_registration_id', 'registrations', ['registration_id'], ['id'])


def downgrade():
    if not has_table('abstracts') or 'registration_id' not in columns('abstracts'):
        return
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.drop_constraint('fk_abstracts_registration_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_abstracts_registration_id'))
        batch_op.drop_column('registration_id')
