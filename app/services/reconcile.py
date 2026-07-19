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

    candidates = (Registration.query
                  .filter(Registration.status.in_(("pending", "processing")),
                          Registration.deleted_at.is_(None))
                  .all())

    checked = 0
    changes: list[dict] = []
    errors: list[str] = []

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
            continue

        old_status = reg.status
        if target == "paid" and reg.status in ("pending", "processing", "failed"):
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

    checked_t, changes_t, errors_t = _reconcile_test_payments(gateway)
    return {"checked": checked + checked_t, "changes": changes + changes_t,
            "errors": errors + errors_t, "error": ""}


# Once one of these is known for a test reference, its lifecycle is over
# and reconciliation stops polling it. Captured/paid payments keep being
# polled — a refund can still follow.
_FINAL_WORDS = ("refunded", "rejected", "cancelled", "chargebacked", "reversed")


def _reconcile_test_payments(gateway) -> tuple[int, list, list]:
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

    checked = 0
    changes: list[dict] = []
    errors: list[str] = []

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

    return checked, changes, errors


def _fetch_status(gateway, reg: Registration):
    """Fetch provider state for *reg*: by payment ID when known, otherwise
    via the most recent hosted checkout session. None = nothing to check."""
    if reg.transaction_id:
        return gateway.get_payment_status(reg.transaction_id)

    checkout = (PaymentEvent.query
                .filter_by(merchant_reference=f"reg_{reg.id}",
                           event_type="checkout.created")
                .order_by(PaymentEvent.id.desc())
                .first())
    if not checkout or not checkout.transaction_id:
        return None
    return gateway.get_checkout_payment(checkout.transaction_id)
