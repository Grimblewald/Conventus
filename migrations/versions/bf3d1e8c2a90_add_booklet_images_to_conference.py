"""add booklet header / footer / background images to conference

Revision ID: bf3d1e8c2a90
Revises: ca48aea332ef
Create Date: 2026-06-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'bf3d1e8c2a90'
down_revision = 'ca48aea332ef'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        for col_name in (
            'booklet_header_filename',
            'booklet_footer_filename',
            'booklet_background_filename',
        ):
            try:
                batch_op.add_column(sa.Column(col_name, sa.String(length=255)))
            except sa.exc.OperationalError:
                pass  # column already exists (db.create_all at boot)


def downgrade():
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        for col_name in (
            'booklet_header_filename',
            'booklet_footer_filename',
            'booklet_background_filename',
        ):
            try:
                batch_op.drop_column(col_name)
            except sa.exc.OperationalError:
                pass
