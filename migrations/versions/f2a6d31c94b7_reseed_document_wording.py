"""reseed document wording that still carries the shipped defaults

The invoice kind inherited the old single-template wording, which reads as a
receipt ("Your payment ... has been received") even though an invoice requests
payment. This replaces the shipped wording for each kind — but ONLY where the
stored text is still exactly a default we shipped, so a society that has edited
its own wording is never overwritten.

Revision ID: f2a6d31c94b7
Revises: e7c2b5a91d38
Create Date: 2026-07-22 18:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a6d31c94b7'
down_revision = 'e7c2b5a91d38'
branch_labels = None
depends_on = None


# Wording this project has shipped as a default at some point. Matched
# literally: anything else is the society's own text and is left alone.
_SHIPPED_SUBJECTS = {
    "invoice": ["Payment Receipt — {conference_title}"],
    "receipt": ["Receipt — {conference_title}"],
    "adjustment": ["Adjustment Note — {conference_title}"],
}

_SHIPPED_BODIES = {
    "invoice": [
        "Dear {user_name},\n\n"
        "Your payment for {conference_title} has been received.\n\n"
        "Registration: {tier_name}\n"
        "Amount: {currency_symbol}{amount} {currency_code}\n"
        "Transaction ID: {transaction_id}\n\n"
        "Thank you,\n{site_name}"
    ],
    "receipt": [
        "Dear {user_name},\n\n"
        "Payment received — this is your receipt for {conference_title}.\n\n"
        "{invoice_type} {transaction_id}\n"
        "Item: {tier_name}\n"
        "Amount paid: {currency_symbol}{amount} {currency_code}\n"
        "Includes GST: {currency_symbol}{gst_amount}\n"
        "Date: {payment_date}\n\n"
        "Thank you,\n{site_name}"
    ],
    "adjustment": [
        "Dear {user_name},\n\n"
        "This is an adjustment note for {conference_title}.\n\n"
        "Reference: {transaction_id}\n"
        "Item: {tier_name}\n"
        "Adjustment amount: {currency_symbol}{amount} {currency_code}\n"
        "Includes GST: {currency_symbol}{gst_amount}\n"
        "Date: {payment_date}\n\n"
        "Any refund due will be returned to your original payment method.\n\n"
        "{site_name}"
    ],
}

_NEW_SUBJECTS = {
    "invoice": "Invoice {transaction_id} — {conference_title}",
    "receipt": "Receipt {transaction_id} — {conference_title}",
    "adjustment": "Adjustment note {transaction_id} — {conference_title}",
}

_NEW_BODIES = {
    "invoice": (
        "Dear {user_name},\n\n"
        "Please find attached invoice {transaction_id} for "
        "{conference_title}.\n\n"
        "Item: {tier_name}\n"
        "Amount due: {currency_symbol}{amount} {currency_code}\n"
        "Due date: {due_date}\n\n"
        "Pay online:\n{payment_link}\n\n"
        "Or by bank transfer:\n{payment_instructions}\n\n"
        "Please quote {transaction_id} with your payment.\n\n"
        "{business_legal_name}"
    ),
    "receipt": (
        "Dear {user_name},\n\n"
        "Thank you — your payment has been received. Your receipt is "
        "attached.\n\n"
        "Item: {tier_name}\n"
        "Amount paid: {currency_symbol}{amount} {currency_code}\n"
        "Date: {payment_date}\n"
        "Reference: {transaction_id}\n\n"
        "{business_legal_name}"
    ),
    "adjustment": (
        "Dear {user_name},\n\n"
        "An adjustment has been made to your payment for "
        "{conference_title}. The adjustment note is attached.\n\n"
        "Item: {tier_name}\n"
        "Amount: {currency_symbol}{amount} {currency_code}\n"
        "Date: {payment_date}\n"
        "Reference: {transaction_id}\n\n"
        "Any refund is returned to the original payment method and can "
        "take a few business days to appear.\n\n"
        "{business_legal_name}"
    ),
}


def upgrade():
    bind = op.get_bind()
    if 'document_template' not in sa.inspect(bind).get_table_names():
        return

    for kind, new_subject in _NEW_SUBJECTS.items():
        row = bind.execute(sa.text(
            "SELECT subject, email_body FROM document_template WHERE kind = :k"
        ), {"k": kind}).fetchone()
        if row is None:
            continue
        subject = row.subject or ""
        body = row.email_body or ""
        if subject and subject not in _SHIPPED_SUBJECTS.get(kind, []):
            continue                      # society-authored — leave it alone
        if body and body not in _SHIPPED_BODIES.get(kind, []):
            continue
        bind.execute(sa.text(
            "UPDATE document_template SET subject = :s, email_body = :b "
            "WHERE kind = :k"
        ), {"s": new_subject, "b": _NEW_BODIES[kind], "k": kind})


def downgrade():
    # Content-only change; the previous wording is restored only where the
    # current text is exactly what this migration wrote.
    bind = op.get_bind()
    if 'document_template' not in sa.inspect(bind).get_table_names():
        return
    for kind, old_bodies in _SHIPPED_BODIES.items():
        bind.execute(sa.text(
            "UPDATE document_template SET subject = :s, email_body = :b "
            "WHERE kind = :k AND email_body = :new"
        ), {"s": _SHIPPED_SUBJECTS[kind][0], "b": old_bodies[0], "k": kind,
            "new": _NEW_BODIES[kind]})
