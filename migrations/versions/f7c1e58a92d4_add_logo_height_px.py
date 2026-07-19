"""add logo height px

Revision ID: f7c1e58a92d4
Revises: e3b9d40a71c5
Create Date: 2026-07-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7c1e58a92d4'
down_revision = 'e3b9d40a71c5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('site_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('logo_height_px', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('site_settings', schema=None) as batch_op:
        batch_op.drop_column('logo_height_px')
