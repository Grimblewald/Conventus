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
    op.execute("""
        CREATE TABLE IF NOT EXISTS organising_committee_members (
            id INTEGER NOT NULL,
            conference_id INTEGER NOT NULL,
            full_name VARCHAR(200) NOT NULL,
            role VARCHAR(120),
            affiliation VARCHAR(200),
            email VARCHAR(200),
            portrait_filename VARCHAR(255),
            display_order INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(conference_id) REFERENCES conferences (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_organising_committee_members_conference_id
        ON organising_committee_members (conference_id)
    """)


def downgrade():
    op.drop_index(
        op.f('ix_organising_committee_members_conference_id'),
        table_name='organising_committee_members',
    )
    op.drop_table('organising_committee_members')
