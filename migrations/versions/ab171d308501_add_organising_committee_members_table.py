"""add organising_committee_members table

Revision ID: ab171d308501
Revises: 2396d1a55dc0
Create Date: 2026-06-06 15:36:39.654831

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ab171d308501'
down_revision = '2396d1a55dc0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'organising_committee_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conference_id', sa.Integer(), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('role', sa.String(length=120), nullable=True),
        sa.Column('affiliation', sa.String(length=200), nullable=True),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('portrait_filename', sa.String(length=255), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['conference_id'], ['conferences.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_organising_committee_members_conference_id'),
        'organising_committee_members', ['conference_id'], unique=False,
    )


def downgrade():
    op.drop_index(
        op.f('ix_organising_committee_members_conference_id'),
        table_name='organising_committee_members',
    )
    op.drop_table('organising_committee_members')
