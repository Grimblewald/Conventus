"""add financial identity, absorbing the per-template business fields

Revision ID: e7c2b5a91d38
Revises: d1f4a2c9b8e6
Create Date: 2026-07-22 13:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7c2b5a91d38'
down_revision = 'd1f4a2c9b8e6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    # Idempotency guard: db.create_all() on boot may already have created
    # this table from the current models, which match this schema exactly.
    if 'financial_identity' not in existing:
        op.create_table('financial_identity',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('legal_name', sa.String(length=200), nullable=True),
        sa.Column('abn', sa.String(length=40), nullable=True),
        sa.Column('gst_registered', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('contact_email', sa.String(length=200), nullable=True),
        sa.Column('payment_instructions', sa.Text(), nullable=True),
        sa.Column('signatory_name', sa.String(length=120), nullable=True),
        sa.Column('signatory_role', sa.String(length=120), nullable=True),
        sa.Column('logo_filename', sa.String(length=80), nullable=True),
        sa.Column('signature_filename', sa.String(length=80), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    # Carry the business details off the invoice template into the single
    # identity row — they describe the issuer, not any one document kind.
    doc_cols = {c['name'] for c in insp.get_columns('document_template')} \
        if 'document_template' in existing else set()
    seeded = bind.execute(sa.text(
        "SELECT 1 FROM financial_identity LIMIT 1")).fetchone()
    if not seeded:
        row = None
        if {'business_number', 'payment_instructions', 'gst_registered'} <= doc_cols:
            row = bind.execute(sa.text(
                "SELECT business_number, payment_instructions, gst_registered "
                "FROM document_template WHERE kind = 'invoice'"
            )).fetchone()
        bind.execute(sa.text(
            "INSERT INTO financial_identity "
            "(legal_name, abn, gst_registered, address, contact_email, "
            " payment_instructions, signatory_name, signatory_role, "
            " logo_filename, signature_filename, updated_at) VALUES "
            "('', :abn, :gst, '', '', :pay, '', '', '', '', CURRENT_TIMESTAMP)"
        ), {
            "abn": (row.business_number if row else "") or "",
            "gst": bool(row.gst_registered) if row else False,
            "pay": (row.payment_instructions if row else "") or "",
        })

    # Drop the absorbed columns from every document kind.
    if doc_cols & {'business_number', 'payment_instructions', 'gst_registered'}:
        with op.batch_alter_table('document_template', schema=None) as batch_op:
            for col in ('business_number', 'payment_instructions', 'gst_registered'):
                if col in doc_cols:
                    batch_op.drop_column(col)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    doc_cols = {c['name'] for c in insp.get_columns('document_template')}

    with op.batch_alter_table('document_template', schema=None) as batch_op:
        if 'business_number' not in doc_cols:
            batch_op.add_column(sa.Column('business_number', sa.String(length=40), nullable=True))
        if 'payment_instructions' not in doc_cols:
            batch_op.add_column(sa.Column('payment_instructions', sa.Text(), nullable=True))
        if 'gst_registered' not in doc_cols:
            batch_op.add_column(sa.Column('gst_registered', sa.Boolean(), nullable=False,
                                          server_default=sa.false()))

    # Best-effort copy back onto the invoice template.
    row = bind.execute(sa.text(
        "SELECT abn, payment_instructions, gst_registered "
        "FROM financial_identity LIMIT 1")).fetchone()
    if row is not None:
        bind.execute(sa.text(
            "UPDATE document_template SET business_number = :abn, "
            "payment_instructions = :pay, gst_registered = :gst "
            "WHERE kind = 'invoice'"
        ), {"abn": row.abn or "", "pay": row.payment_instructions or "",
            "gst": bool(row.gst_registered)})

    op.drop_table('financial_identity')
