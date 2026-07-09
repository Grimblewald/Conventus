"""optional abstract author and website url

Abstracts can now be admin-entered without an author account
(user_id nullable) and carry an optional presenter website link.

Revision ID: f3a81c60d2e4
Revises: c8e588590700
Create Date: 2026-07-09 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a81c60d2e4'
down_revision = 'c8e588590700'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.alter_column('user_id',
                              existing_type=sa.Integer(),
                              nullable=True)
        batch_op.add_column(
            sa.Column('website_url', sa.String(length=300),
                      nullable=True, server_default=''))


def downgrade():
    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.drop_column('website_url')
        batch_op.alter_column('user_id',
                              existing_type=sa.Integer(),
                              nullable=False)
