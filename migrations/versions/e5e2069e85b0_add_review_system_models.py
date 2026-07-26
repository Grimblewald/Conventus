"""add review system models

Revision ID: e5e2069e85b0
Revises: 1da8cf69604f
Create Date: 2026-07-07 14:50:02.904916

Idempotent: the baseline builds the schema from the current models, so these
tables, indexes and columns may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import (
    add_columns, create_index, create_table, drop_columns, drop_index, drop_table,
)


# revision identifiers, used by Alembic.
revision = 'e5e2069e85b0'
down_revision = '1da8cf69604f'
branch_labels = None
depends_on = None


def upgrade():
    create_table('conference_reviewers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conference_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('expertise', sa.Text(), nullable=True),
    sa.Column('max_reviews', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['conference_id'], ['conferences.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conference_id', 'user_id', name='uq_conference_reviewer')
    )
    create_index('ix_conference_reviewers_conference_id', 'conference_reviewers', ['conference_id'])
    create_index('ix_conference_reviewers_user_id', 'conference_reviewers', ['user_id'])

    create_table('review_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('abstract_id', sa.Integer(), nullable=False),
    sa.Column('reviewer_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('recommendation', sa.String(length=20), nullable=True),
    sa.Column('comments_author', sa.Text(), nullable=True),
    sa.Column('comments_chair', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('claimed_at', sa.DateTime(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['abstract_id'], ['abstracts.id'], ),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('abstract_id', 'reviewer_id', name='uq_review_assignment')
    )
    create_index('ix_review_assignments_abstract_id', 'review_assignments', ['abstract_id'])
    create_index('ix_review_assignments_reviewer_id', 'review_assignments', ['reviewer_id'])

    add_columns('conferences',
                sa.Column('reviewers_per_paper', sa.Integer(), nullable=False, server_default='2'),
                sa.Column('review_deadline', sa.Date(), nullable=True))


def downgrade():
    drop_columns('conferences', 'review_deadline', 'reviewers_per_paper')
    drop_index('ix_review_assignments_reviewer_id', 'review_assignments')
    drop_index('ix_review_assignments_abstract_id', 'review_assignments')
    drop_table('review_assignments')
    drop_index('ix_conference_reviewers_user_id', 'conference_reviewers')
    drop_index('ix_conference_reviewers_conference_id', 'conference_reviewers')
    drop_table('conference_reviewers')
