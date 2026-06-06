"""add dynamic_roles to committee_member

Revision ID: 40f8684e20c4
Revises: ab171d308501
Create Date: 2026-06-06 15:41:11.921955

"""
from alembic import op
import sqlalchemy as sa


revision = '40f8684e20c4'
down_revision = 'ab171d308501'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('committee_members', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('dynamic_roles', sa.JSON(), nullable=True))
        except sa.exc.OperationalError:
            pass


def downgrade():
    with op.batch_alter_table('committee_members', schema=None) as batch_op:
        try:
            batch_op.drop_column('dynamic_roles')
        except sa.exc.OperationalError:
            pass
