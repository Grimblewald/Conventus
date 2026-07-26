"""add past_boards tables and board_term fields

Revision ID: ca48aea332ef
Revises: 40f8684e20c4
Create Date: 2026-06-06 16:26:02.177749

Idempotent: the baseline builds the schema from the current models, so these
tables and columns may already exist. See app/migration_guards.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns


revision = 'ca48aea332ef'
down_revision = '40f8684e20c4'
branch_labels = None
depends_on = None

BOARD_TERM_COLUMNS = (
    ('board_term_start', sa.Date),
    ('board_term_interval_months', sa.Integer),
    ('board_last_archived_at', sa.DateTime),
)


def upgrade():
    # Past boards table
    op.execute("""
        CREATE TABLE IF NOT EXISTS past_boards (
            id INTEGER NOT NULL,
            label VARCHAR(120) NOT NULL,
            term_start DATE,
            term_end DATE,
            display_order INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    # Past board members table
    op.execute("""
        CREATE TABLE IF NOT EXISTS past_board_members (
            id INTEGER NOT NULL,
            past_board_id INTEGER NOT NULL,
            full_name VARCHAR(200) NOT NULL,
            title VARCHAR(40),
            role VARCHAR(120),
            affiliation VARCHAR(200),
            position VARCHAR(200),
            interests TEXT,
            orcid VARCHAR(40),
            scholar_url VARCHAR(400),
            website_url VARCHAR(400),
            portrait_filename VARCHAR(255),
            portrait_alt_text VARCHAR(255),
            display_order INTEGER NOT NULL,
            PRIMARY KEY (id),
            FOREIGN KEY(past_board_id) REFERENCES past_boards (id) ON DELETE CASCADE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_past_board_members_past_board_id
        ON past_board_members (past_board_id)
    """)

    # SiteSettings board term fields
    add_columns('site_settings',
                *(sa.Column(name, type_(), nullable=True)
                  for name, type_ in BOARD_TERM_COLUMNS))


def downgrade():
    drop_columns('site_settings',
                 *(name for name, _ in reversed(BOARD_TERM_COLUMNS)))

    op.execute("DROP INDEX IF EXISTS ix_past_board_members_past_board_id")
    op.drop_table('past_board_members')
    op.drop_table('past_boards')
