"""return registrations cancelled by a payment webhook to pending

Revision ID: d5b719ac3e08
Revises: c2f7b41e9a68
Create Date: 2026-08-24 00:00:00.000000

A cancelled payment attempt used to cancel the registration itself, which left
the fee owing on a record that read as cancelled and made the durable pay link
refuse it. Only rows carrying positive evidence of that path are touched: a
cancelled status, a payment.cancelled event, and no manual.cancelled event of
the kind an administrator's own cancellation records. Anything cancelled by a
person is left alone.
"""
from alembic import op
import sqlalchemy as sa

from app.migration_guards import has_table


revision = 'd5b719ac3e08'
down_revision = 'c2f7b41e9a68'
branch_labels = None
depends_on = None


def upgrade():
    if not (has_table('registrations') and has_table('payment_events')):
        return
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE registrations SET status = 'pending'
         WHERE deleted_at IS NULL
           AND status = 'cancelled'
           AND EXISTS (SELECT 1 FROM payment_events e
                        WHERE e.registration_id = registrations.id
                          AND e.event_type = 'payment.cancelled')
           AND NOT EXISTS (SELECT 1 FROM payment_events e
                            WHERE e.registration_id = registrations.id
                              AND e.event_type = 'manual.cancelled')
    """))


def downgrade():
    # The rows are indistinguishable from any other pending registration once
    # corrected, and re-cancelling them would restore the defect.
    pass
