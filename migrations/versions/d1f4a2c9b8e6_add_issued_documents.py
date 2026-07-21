"""add issued_documents regeneration store

Revision ID: d1f4a2c9b8e6
Revises: b3f7a9c1e2d4
Create Date: 2026-07-21 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1f4a2c9b8e6'
down_revision = 'b3f7a9c1e2d4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    # Idempotency guard: db.create_all() on boot may have already created
    # issued_documents (with its indexes) from the current models, which match
    # this schema exactly — so only create when it's missing.
    if 'issued_documents' not in existing:
        op.create_table('issued_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=120), nullable=False),
        sa.Column('recipient', sa.String(length=200), nullable=True),
        sa.Column('amount', sa.Integer(), nullable=True),
        sa.Column('vars_json', sa.Text(), nullable=True),
        sa.Column('template_json', sa.Text(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_issued_documents_reference'),
                        'issued_documents', ['reference'], unique=False)
        op.create_index(op.f('ix_issued_documents_issued_at'),
                        'issued_documents', ['issued_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_issued_documents_issued_at'),
                  table_name='issued_documents')
    op.drop_index(op.f('ix_issued_documents_reference'),
                  table_name='issued_documents')
    op.drop_table('issued_documents')
