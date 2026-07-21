"""add document template, replacing invoice template

Revision ID: b3f7a9c1e2d4
Revises: a1d5c3e87b42
Create Date: 2026-07-21 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f7a9c1e2d4'
down_revision = 'a1d5c3e87b42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('document_template',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=True),
    sa.Column('email_body', sa.Text(), nullable=True),
    sa.Column('from_name', sa.String(length=120), nullable=True),
    sa.Column('from_email', sa.String(length=200), nullable=True),
    sa.Column('footer_text', sa.String(length=400), nullable=True),
    sa.Column('pdf_body', sa.Text(), nullable=True),
    sa.Column('business_number', sa.String(length=40), nullable=True),
    sa.Column('payment_instructions', sa.Text(), nullable=True),
    sa.Column('gst_registered', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_document_template_kind'), 'document_template', ['kind'], unique=True)

    # Carry the existing invoice_template row (id=1) into the consolidated
    # table as the 'invoice' kind. body_html is intentionally dropped; the
    # new pdf_body starts empty (renderer lands in a later step). receipt and
    # adjustment rows are left to lazy-seed on first access.
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT subject, body_text, from_name, from_email, footer_text, "
        "business_number, payment_instructions, gst_registered, updated_at "
        "FROM invoice_template WHERE id = 1"
    )).fetchone()
    if row is not None:
        bind.execute(sa.text(
            "INSERT INTO document_template "
            "(kind, subject, email_body, from_name, from_email, footer_text, "
            "pdf_body, business_number, payment_instructions, gst_registered, "
            "updated_at) VALUES "
            "('invoice', :subject, :email_body, :from_name, :from_email, "
            ":footer_text, '', :business_number, :payment_instructions, "
            ":gst_registered, :updated_at)"
        ), {
            "subject": row.subject,
            "email_body": row.body_text,
            "from_name": row.from_name,
            "from_email": row.from_email,
            "footer_text": row.footer_text,
            "business_number": row.business_number,
            "payment_instructions": row.payment_instructions,
            "gst_registered": row.gst_registered,
            "updated_at": row.updated_at,
        })

    op.drop_table('invoice_template')


def downgrade():
    op.create_table('invoice_template',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=True),
    sa.Column('body_text', sa.Text(), nullable=True),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('from_name', sa.String(length=120), nullable=True),
    sa.Column('from_email', sa.String(length=200), nullable=True),
    sa.Column('footer_text', sa.String(length=400), nullable=True),
    sa.Column('business_number', sa.String(length=40), nullable=True),
    sa.Column('payment_instructions', sa.Text(), nullable=True),
    sa.Column('gst_registered', sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    # Best-effort copy back of the invoice kind; body_html is restored as NULL.
    bind = op.get_bind()
    row = bind.execute(sa.text(
        "SELECT subject, email_body, from_name, from_email, footer_text, "
        "business_number, payment_instructions, gst_registered, updated_at "
        "FROM document_template WHERE kind = 'invoice'"
    )).fetchone()
    if row is not None:
        bind.execute(sa.text(
            "INSERT INTO invoice_template "
            "(id, subject, body_text, body_html, from_name, from_email, "
            "footer_text, business_number, payment_instructions, "
            "gst_registered, updated_at) VALUES "
            "(1, :subject, :body_text, NULL, :from_name, :from_email, "
            ":footer_text, :business_number, :payment_instructions, "
            ":gst_registered, :updated_at)"
        ), {
            "subject": row.subject,
            "body_text": row.email_body,
            "from_name": row.from_name,
            "from_email": row.from_email,
            "footer_text": row.footer_text,
            "business_number": row.business_number,
            "payment_instructions": row.payment_instructions,
            "gst_registered": row.gst_registered,
            "updated_at": row.updated_at,
        })

    op.drop_index(op.f('ix_document_template_kind'), table_name='document_template')
    op.drop_table('document_template')
