"""add max_abstracts_per_user to conference

Revision ID: 099d052323c0
Revises: 9676ce7e8ae8
Create Date: 2026-06-06 15:30:00.768105

"""
from alembic import op
import sqlalchemy as sa


revision = '099d052323c0'
down_revision = '9676ce7e8ae8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('max_abstracts_per_user', sa.Integer(), nullable=True))
        except sa.exc.OperationalError:
            pass


def downgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        try:
            batch_op.drop_column('max_abstracts_per_user')
        except sa.exc.OperationalError:
            pass
