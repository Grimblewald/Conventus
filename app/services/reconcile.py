"""Reconcile local payment state against the provider.

Webhooks can be missed (site restarting during delivery, retries
exhausted). For every unsettled registration that actually started a
checkout, this fetches the payment's current state from Worldline and
applies the same guarded transitions the webhook handler would have —
sending the invoice/refund emails that would have gone out — and records
every change in the payment_events ledger as a ``reconcile.*`` event.
"""
from __future__ import annotations

import logging

from ..extensions import db
from ..models import PaymentEvent, Registration, record_payment_event

log = logging.getLogger(__name__)

# Provider payment status → our registration status. Statuses absent here
# (CREATED, REDIRECTED, PENDING_PAYMENT, AUTHORIZATION_REQUESTED, …) are
# non-terminal and cause no change; CHARGEBACKED/REVERSED are deliberately
# excluded — disputes stay manual.
_STATUS_MAP = {
    "PAID": "paid",
    "CAPTURED": "captured-as-paid",
    "REFUNDED": "refunded",
    "REJECTED": "failed",
    "REJECTED_CAPTURE": "failed",
    "CANCELLED": "cancelled",
    "PENDING_CAPTURE": "processing",
    "CAPTURE_REQUESTED": "processing",
}


def reconcile_payments() -> dict:
    """Check every unsettled registration against Worldline.

    Returns {"checked": int, "changes": [...], "errors": [...], "error": str}.
    """
    from .payments import _active_gateway

    gateway = _active_gateway()
    if not gateway:
        return {"checked": 0, "changes": [], "errors": [],
                "error": "No enabled payment gateway."}

    # `failed` and `cancelled` are included because a first attempt that did
    # not go through says nothing about a second that did — and if that second
    # webhook was the one missed, this sweep is the only thing that would ever
    # notice. Settled states are deliberately absent: a registration that is
    # already paid or refunded is not something to re-decide from here.
    candidates = (Registration.query
                  .filter(Registration.status.in_(("pending", "processing",
                                                   "failed", "cancelled")),
                          Registration.deleted_at.is_(None))
                  .all())

    checked = 0
    changes: list[dict] = []
    errors: list[str] = []
    unchanged: list[str] = []

    for reg in candidates:
        status = _fetch_status(gateway, reg)
        if status is None:
            continue  # never started a checkout — nothing to ask about
        checked += 1
        if status.error:
            errors.append(f"reg {reg.id}: {status.error}")
            continue

        target = _STATUS_MAP.get((status.raw_status or "").upper())
        if target == "captured-as-paid":
            target = "paid"
        if not target or target == reg.status:
            unchanged.append(f"reg {reg.id}: {status.raw_status or 'NO_PAYMENT'}")
            continue

        old_status = reg.status
        if target == "paid" and reg.status in ("pending", "processing",
                                               "failed", "cancelled"):
            reg.status = "paid"
        elif target == "refunded":
            reg.status = "refunded"
        elif target in ("failed", "cancelled") and reg.status in ("pending", "processing"):
            reg.status = target
        elif target == "processing" and reg.status == "pending":
            reg.status = "processing"
        else:
            continue

        reg.transaction_id = reg.transaction_id or status.transaction_id
        reg.last_webhook_event = f"reconcile.{status.raw_status.lower()}"
        db.session.commit()

        record_payment_event(
            transaction_id=status.transaction_id or reg.transaction_id or "",
            merchant_reference=f"reg_{reg.id}",
            registration_id=reg.id,
            event_type=f"reconcile.{status.raw_status.lower()}",
            amount=status.amount,
            note=f"status {old_status} → {reg.status} (reconciliation)",
        )

        if reg.status in ("paid", "refunded"):
            try:
                from .invoice import send_invoice_email
                send_invoice_email(reg)
            except Exception:
                log.exception("Invoice email failed during reconciliation for reg %d", reg.id)

        changes.append({
            "reg_id": reg.id,
            "email": reg.user.email if reg.user else "unknown",
            "old": old_status,
            "new": reg.status,
            "raw": status.raw_status,
        })

    checked_t, changes_t, errors_t, unchanged_t = _reconcile_test_payments(gateway)
    return {"checked": checked + checked_t, "changes": changes + changes_t,
            "errors": errors + errors_t, "unchanged": unchanged + unchanged_t,
            "error": ""}


# Once one of these is known for a test reference, its lifecycle is over
# and reconciliation stops polling it. Captured/paid payments keep being
# polled — a refund can still follow.
_FINAL_WORDS = ("refunded", "rejected", "cancelled", "chargebacked", "reversed")


def _reconcile_test_payments(gateway) -> tuple[int, list, list, list]:
    """Sweep recent admin test payments (test_* references): fetch their
    current provider state and record it in the ledger when the ledger
    doesn't reflect it yet (i.e. the webhook was missed)."""
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=30)
    by_ref: dict[str, list[PaymentEvent]] = {}
    events = (PaymentEvent.query
              .filter(PaymentEvent.merchant_reference.like("test\\_%", escape="\\"),
                      PaymentEvent.created_at >= cutoff)
              .order_by(PaymentEvent.id.asc())
              .all())
    for e in events:
        by_ref.setdefault(e.merchant_reference, []).append(e)

    _add_pre_ledger_test_refs(by_ref, cutoff)

    checked = 0
    changes: list[dict] = []
    errors: list[str] = []
    unchanged: list[str] = []

    for ref, ref_events in by_ref.items():
        seen_types = {e.event_type for e in ref_events}
        if any(t.rsplit(".", 1)[-1] in _FINAL_WORDS for t in seen_types):
            continue

        # Poll via a payment (or the originating checkout) — never via a
        # refund ID that a previous reconciliation recorded.
        poll = None
        for e in ref_events:
            if e.transaction_id and (e.event_type == "checkout.created"
                                     or e.event_type.startswith("payment.")):
                poll = e
        if poll is None:
            continue

        if poll.event_type == "checkout.created":
            status = gateway.get_checkout_payment(poll.transaction_id)
        else:
            status = gateway.get_payment_status(poll.transaction_id)
        checked += 1
        if status.error:
            errors.append(f"test payment {ref}: {status.error}")
            continue

        raw = (status.raw_status or "").upper()
        already_known = any(raw.lower() in t for t in seen_types)
        terminal = raw in _STATUS_MAP or raw in ("CHARGEBACKED", "REVERSED")
        if not terminal or already_known:
            unchanged.append(f"{ref}: {raw or 'NO_PAYMENT'}")
            continue

        record_payment_event(
            transaction_id=status.transaction_id or poll.transaction_id,
            merchant_reference=ref,
            event_type=f"reconcile.{raw.lower()}",
            amount=status.amount,
            note="test payment state found by reconciliation (webhook missed?)",
        )
        changes.append({"reg_id": None, "email": "test payment",
                        "old": poll.event_type, "new": ref,
                        "raw": raw, "test_ref": ref})

    return checked, changes, errors, unchanged


def _add_pre_ledger_test_refs(by_ref: dict, cutoff) -> None:
    """Test payments made before the payment_events ledger existed live only
    in the audit log. Materialize their event history into the ledger (with
    the original timestamps) so they appear on the Transactions page and can
    be polled/cancelled like any other reference. Runs once per reference:
    materialized refs are in the ledger on subsequent sweeps. DELETE once
    every pre-ledger test reference has aged out of the cutoff window."""
    import re

    from .jinja_filters import parse_cents
    from ..models.audit import AuditLog

    rows = (AuditLog.query
            .filter(AuditLog.action == "financial.test_payment_event",
                    AuditLog.created_at >= cutoff)
            .order_by(AuditLog.id.asc())
            .all())
    pattern = re.compile(r"Test payment (test_\w+): ([\w.]+), "
                         r"\$([\d.,]+|\?), transaction (\S+)")
    ledger_refs = set(by_ref)
    materialized = False
    for row in rows:
        m = pattern.match(row.summary or "")
        if not m:
            continue
        ref, etype, amount_raw, txn = m.groups()
        if ref in ledger_refs or txn in ("n/a", ""):
            continue
        try:
            amount = None if amount_raw == "?" else parse_cents(amount_raw)
        except ValueError:
            amount = None
        evt = PaymentEvent(
            merchant_reference=ref, event_type=etype, transaction_id=txn,
            amount=amount, note="backfilled from audit log",
            created_at=row.created_at,
        )
        db.session.add(evt)
        materialized = True
        by_ref.setdefault(ref, []).append(evt)
    if materialized:
        db.session.commit()


def _fetch_status(gateway, reg: Registration):
    """Fetch provider state for *reg*: by payment ID when known, otherwise
    via the most recent hosted checkout session. None = nothing to check."""
    if reg.transaction_id:
        return gateway.get_payment_status(reg.transaction_id)

    checkout = (PaymentEvent.query
                .filter(db.or_(
                    PaymentEvent.merchant_reference == f"reg_{reg.id}",
                    PaymentEvent.merchant_reference.like(f"reg\\_{reg.id}-%", escape="\\")),
                        PaymentEvent.event_type == "checkout.created")
                .order_by(PaymentEvent.id.desc())
                .first())
    if not checkout or not checkout.transaction_id:
        return None
    return gateway.get_checkout_payment(checkout.transaction_id)
