"""add hero_image_mode to conference

Revision ID: 1355ee08665c
Revises: 4a1b2c3d4e5f
Create Date: 2026-06-06 14:11:54.739648

"""
from alembic import op
import sqlalchemy as sa


revision = '1355ee08665c'
down_revision = '4a1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        try:
            batch_op.add_column(sa.Column('hero_image_mode', sa.String(length=16),
                                 nullable=False, server_default='cover'))
        except sa.exc.OperationalError:
            pass  # column already exists (db.create_all at boot)


def downgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        try:
            batch_op.drop_column('hero_image_mode')
        except sa.exc.OperationalError:
            pass
