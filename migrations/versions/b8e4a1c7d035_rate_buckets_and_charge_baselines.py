"""rate buckets, plus charge-line and pay-token backfill

Revision ID: b8e4a1c7d035
Revises: a7d31f6c02b9
Create Date: 2026-08-19 00:00:00.000000

Two things, both prerequisites for reads that no longer write.

`amount_due` used to seed a registration's opening charge line the first time
anything asked what was owed — which made loading the pay page write to the
ledger. The seeding moves to registration save, so every row that predates
charge lines needs its baseline once, here. Pay tokens moved the same way and
are backfilled for the same reason.

Idempotent: the baseline builds the schema from the current models, so the
table may already exist. See app/migration_guards.
"""
import secrets

from alembic import op
import sqlalchemy as sa

from app.migration_guards import create_table, drop_table, has_table


revision = 'b8e4a1c7d035'
down_revision = 'a7d31f6c02b9'
branch_labels = None
depends_on = None


CHARGE_EVENTS = ("registration.payment_due", "registration.no_payment_due")


def upgrade():
    create_table(
        'rate_buckets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(length=160), nullable=False, unique=True),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
    )

    bind = op.get_bind()
    if not (has_table('registrations') and has_table('payment_events')):
        return

    charged = {r[0] for r in bind.execute(sa.text(
        "SELECT DISTINCT registration_id FROM payment_events "
        "WHERE registration_id IS NOT NULL AND event_type IN (:a, :b)"
    ), {"a": CHARGE_EVENTS[0], "b": CHARGE_EVENTS[1]})}

    rows = bind.execute(sa.text(
        "SELECT id, amount, tier_name, pay_token FROM registrations "
        "WHERE deleted_at IS NULL"
    )).fetchall()

    for reg_id, amount, tier_name, pay_token in rows:
        if reg_id not in charged and amount:
            ref = f"REG-{reg_id:06d}"
            bind.execute(sa.text(
                "INSERT INTO payment_events (created_at, transaction_id, "
                "merchant_reference, registration_id, event_type, amount, note) "
                "VALUES (CURRENT_TIMESTAMP, '', :ref, :rid, :etype, :amt, :note)"
            ), {"ref": ref, "rid": reg_id, "etype": CHARGE_EVENTS[0],
                "amt": amount,
                "note": f"{ref}: opening balance for {tier_name or 'registration'}"})
        if not pay_token:
            bind.execute(sa.text(
                "UPDATE registrations SET pay_token = :t WHERE id = :rid"
            ), {"t": secrets.token_urlsafe(32), "rid": reg_id})

    # Balances are derived from the ledger, so they have to be restated once
    # the baselines exist — otherwise a backfilled registration reads as fully
    # paid at zero until its next event lands.
    _recompute_outstanding(bind)


def _recompute_outstanding(bind):
    settles = ("captured", "paid")
    reverses = ("refunded",)
    namespaces = ("payment", "manual", "reconcile", "refund")

    rows = bind.execute(sa.text(
        "SELECT registration_id, event_type, amount, transaction_id "
        "FROM payment_events WHERE registration_id IS NOT NULL ORDER BY id ASC"
    )).fetchall()

    totals: dict[int, int] = {}
    seen: set[tuple[int, str, int]] = set()
    for reg_id, event_type, amount, txn in rows:
        if not amount:
            continue
        if event_type in CHARGE_EVENTS:
            delta = amount
        else:
            namespace, _, verb = (event_type or "").partition(".")
            if namespace not in namespaces:
                continue
            if verb in settles:
                delta = -amount
            elif verb in reverses:
                delta = amount
            else:
                continue
        if txn:
            key = (reg_id, txn, 1 if delta > 0 else -1)
            if key in seen:
                continue
            seen.add(key)
        totals[reg_id] = totals.get(reg_id, 0) + delta

    for reg_id, total in totals.items():
        bind.execute(sa.text(
            "UPDATE registrations SET amount_outstanding = :t WHERE id = :rid"
        ), {"t": total, "rid": reg_id})


def downgrade():
    drop_table('rate_buckets')
