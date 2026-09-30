"""split accepted into oral and poster; per-conference speaker visibility

Revision ID: 20649c8aa1d5
Revises: e1c47af8b920
Create Date: 2026-09-30 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so the
column may already exist. See app/migration_guards.

A plain "accepted" abstract becomes "accepted_oral": it was listed on the
conference page, and oral is the acceptance that is listed by default.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns, has_table


revision = '20649c8aa1d5'
down_revision = 'e1c47af8b920'
branch_labels = None
depends_on = None


def upgrade():
    add_columns('conferences',
                sa.Column('speaker_visibility', sa.JSON(), nullable=True))
    if has_table('abstracts'):
        op.get_bind().execute(sa.text(
            "UPDATE abstracts SET status = 'accepted_oral' "
            "WHERE status = 'accepted'"))


def downgrade():
    # The older code knows only one acceptance, so both collapse into it.
    if has_table('abstracts'):
        op.get_bind().execute(sa.text(
            "UPDATE abstracts SET status = 'accepted' "
            "WHERE status IN ('accepted_oral', 'accepted_poster')"))
    drop_columns('conferences', 'speaker_visibility')
