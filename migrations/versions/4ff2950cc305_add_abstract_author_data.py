"""structured authors and affiliations on abstracts

Revision ID: 4ff2950cc305
Revises: 20649c8aa1d5
Create Date: 2026-10-21 00:00:00.000000

Idempotent: the baseline builds the schema from the current models, so the
column may already exist. See app/migration_guards.

Every abstract without structured author data gets it, read from its author
text through the same parser the pages use, so a row typed by hand ("1,2"
with "A; B") comes through as the author meant it. That parser can change
after this migration is written; tests/test_authors.py pins what it produces
for the shapes this migration meets.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import add_columns, drop_columns, has_table


revision = '4ff2950cc305'
down_revision = '20649c8aa1d5'
branch_labels = None
depends_on = None


def upgrade():
    # Imported here: alembic loads every migration to list revisions, and only
    # this one needs the parser.
    from app.services.authors import parse, to_data

    add_columns('abstracts', sa.Column('author_data', sa.JSON(), nullable=True))
    if not has_table('abstracts'):
        return
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, authors, presenting_author_index FROM abstracts "
        "WHERE author_data IS NULL")).fetchall()
    update = sa.text("UPDATE abstracts SET author_data = :data WHERE id = :id"
                     ).bindparams(sa.bindparam("data", type_=sa.JSON()))
    for row_id, authors, presenting in rows:
        parsed = parse(authors, presenting)
        if parsed.authors:
            bind.execute(update, {"data": to_data(parsed), "id": row_id})


def downgrade():
    # The author text is kept in step on every save, so nothing is lost.
    drop_columns('abstracts', 'author_data')
