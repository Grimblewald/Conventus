"""add is_contactable to committee_member

Revision ID: 9676ce7e8ae8
Revises: 1355ee08665c
Create Date: 2026-06-06 15:27:09.473257

"""
from alembic import op
import sqlalchemy as sa


revision = '9676ce7e8ae8'
down_revision = '1355ee08665c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('committee_members', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('is_contactable', sa.Boolean(),
                                 nullable=False, server_default=sa.text('0')))
        except sa.exc.OperationalError:
            pass


def downgrade():
    with op.batch_alter_table('committee_members', schema=None) as batch_op:
        try:
            batch_op.drop_column('is_contactable')
        except sa.exc.OperationalError:
            pass
