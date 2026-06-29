"""add form schemas, custom data, sub events, form templates, early bird pricing

Revision ID: 1b5ead423541
Revises: bf3d1e8c2a90
Create Date: 2026-06-28 17:50:37.611854

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b5ead423541'
down_revision = 'bf3d1e8c2a90'
branch_labels = None
depends_on = None


def upgrade():
    # New columns on existing tables
    with op.batch_alter_table('conferences', schema=None) as batch_op:
        batch_op.add_column(sa.Column('registration_form_schema', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('abstract_form_schema', sa.JSON(), nullable=True))

    with op.batch_alter_table('price_tiers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('early_bird_amount', sa.Integer(), nullable=True))

    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('custom_data', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('sub_events', sa.JSON(), nullable=True))

    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('custom_data', sa.JSON(), nullable=True))

    # New tables
    op.create_table('form_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('form_type', sa.String(length=20), nullable=False),
    sa.Column('schema', sa.JSON(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('sub_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conference_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('eligibility_note', sa.Text(), nullable=True),
    sa.Column('preference_schema', sa.JSON(), nullable=True),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['conference_id'], ['conferences.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sub_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sub_events_conference_id'), ['conference_id'], unique=False)


def downgrade():
    with op.batch_alter_table('sub_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sub_events_conference_id'))
    op.drop_table('sub_events')
    op.drop_table('form_templates')

    with op.batch_alter_table('abstracts', schema=None) as batch_op:
        batch_op.drop_column('custom_data')

    with op.batch_alter_table('registrations', schema=None) as batch_op:
        batch_op.drop_column('sub_events')
        batch_op.drop_column('custom_data')

    with op.batch_alter_table('price_tiers', schema=None) as batch_op:
        batch_op.drop_column('early_bird_amount')

    with op.batch_alter_table('conferences', schema=None) as batch_op:
        batch_op.drop_column('abstract_form_schema')
        batch_op.drop_column('registration_form_schema')
