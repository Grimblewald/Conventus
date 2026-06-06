"""add profile_picture to abstract

Revision ID: 2396d1a55dc0
Revises: 099d052323c0
Create Date: 2026-06-06 15:32:48.932617

"""
from alembic import op
import sqlalchemy as sa


revision = '2396d1a55dc0'
down_revision = '099d052323c0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('profile_picture_filename', sa.String(length=240), nullable=True))
        except sa.exc.OperationalError:
            pass


def downgrade():
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        try:
            batch_op.drop_column('profile_picture_filename')
        except sa.exc.OperationalError:
            pass
