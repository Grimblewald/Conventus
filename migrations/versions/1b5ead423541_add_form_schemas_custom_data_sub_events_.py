"""add form schemas, custom data, sub events, form templates, early bird pricing

Revision ID: 1b5ead423541
Revises: bf3d1e8c2a90
Create Date: 2026-06-28 17:50:37.611854

Idempotent: the baseline builds the schema from the current models, so these
objects may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import (
    add_columns, create_index, create_table, drop_columns, drop_index, drop_table,
)


# revision identifiers, used by Alembic.
revision = '1b5ead423541'
down_revision = 'bf3d1e8c2a90'
branch_labels = None
depends_on = None


def upgrade():
    # New columns on existing tables
    add_columns('conferences',
                sa.Column('registration_form_schema', sa.JSON(), nullable=True),
                sa.Column('abstract_form_schema', sa.JSON(), nullable=True))
    add_columns('price_tiers',
                sa.Column('early_bird_amount', sa.Integer(), nullable=True))
    add_columns('registrations',
                sa.Column('custom_data', sa.JSON(), nullable=True),
                sa.Column('sub_events', sa.JSON(), nullable=True))
    add_columns('abstracts',
                sa.Column('custom_data', sa.JSON(), nullable=True))

    # New tables
    create_table('form_templates',
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

    create_table('sub_events',
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
    create_index('ix_sub_events_conference_id', 'sub_events', ['conference_id'])


def downgrade():
    drop_index('ix_sub_events_conference_id', 'sub_events')
    drop_table('sub_events')
    drop_table('form_templates')
    drop_columns('abstracts', 'custom_data')
    drop_columns('registrations', 'sub_events', 'custom_data')
    drop_columns('price_tiers', 'early_bird_amount')
    drop_columns('conferences', 'abstract_form_schema', 'registration_form_schema')
