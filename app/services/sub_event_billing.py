"""Charging for a conference's sub-events (workshops, dinners, excursions).

A registration's answer for each sub-event lives in `Registration.sub_events`
under `SubEvent.key`:

  {"attending": bool, <preference answers>, "charged": cents, "name": str}

`charged` and `name` appear once the registration is billed for it. The price
is struck then and kept on the entry, so a later price change re-prices
nobody already billed.

A tentative sub-event records attendance without charging. Billing starts
once, for everyone already attending, through `start_billing`; from then on
whoever opts in is charged when they save their registration.
"""
from __future__ import annotations

from ..extensions import db
from ..models import Registration
from ..models.payment_event import amount_received

# Registrations that are someone's deliberate end state are never billed.
_NOT_BILLED = ("cancelled", "refunded")


def asked(schema, sub_events) -> list:
    """The sub-events the registration form asks about, in conference order.

    Only these get an answer recorded: storing "no" for an event the form
    never showed would read as a refusal nobody made.
    """
    ids = {f.get("sub_event_id")
           for section in (schema or {}).get("sections", [])
           for f in section.get("fields", [])
           if f.get("type") == "sub-event"}
    return [se for se in sub_events if se.id in ids]


def paid_for(reg, entry) -> bool:
    """Whether *reg* has paid for the sub-event *entry* records.

    Once it has, the member cannot withdraw from it on the form: doing so
    would leave a credit for the treasurer to notice and refund by hand.
    """
    return bool(reg is not None and reg.id and isinstance(entry, dict)
                and entry.get("attending") and entry.get("charged")
                and amount_received(reg.id) >= reg.total_price)


def paid_for_event(reg, se) -> bool:
    """`paid_for` by event, for the registration form."""
    return paid_for(reg, (getattr(reg, "sub_events", None) or {}).get(se.key))


def collect(form, events, existing, reg=None) -> dict:
    """The sub-event answers a registration form posted, as stored.

    Answers for events the form does not ask about are kept as they were.
    Opting into a billed event strikes its price; one already paid for
    stays attended whatever was posted.
    """
    data = dict(existing or {})
    for se in events:
        old = data.get(se.key) if isinstance(data.get(se.key), dict) else {}
        locked = paid_for(reg, old)
        attending = locked or form.get(f"_sub_event_{se.key}_attending") == "yes"
        entry = {"attending": attending}
        if attending:
            for pf in (se.preference_schema or {}).get("fields", []):
                name = f"_sub_event_{se.key}_{pf.get('key', '')}"
                entry[pf.get("key", "")] = (form.getlist(name)
                                            if pf.get("type") == "checkbox-group"
                                            else form.get(name, ""))
            if old.get("charged"):
                entry["charged"], entry["name"] = old["charged"], old.get("name", se.name)
            elif se.billed:
                entry["charged"], entry["name"] = se.price, se.name
        data[se.key] = entry
    return data


def awaiting_billing(se) -> list:
    """Registrations attending tentative *se* that billing it would charge."""
    regs = (Registration.query
            .filter(Registration.conference_id == se.conference_id,
                    Registration.deleted_at.is_(None),
                    Registration.status.notin_(_NOT_BILLED))
            .order_by(Registration.id)
            .all())
    out = []
    for reg in regs:
        entry = (reg.sub_events or {}).get(se.key)
        if isinstance(entry, dict) and entry.get("attending") and not entry.get("charged"):
            out.append(reg)
    return out


def start_billing(se, *, note: str = "", send_invoices: bool = True) -> list[int]:
    """Stop *se* being tentative and charge everyone already attending.

    Each is charged the price as it stands now, returned to pending if they
    had paid everything else, and — when *send_invoices* — emailed an invoice
    for their balance after this request, with *note* at the top. Returns the
    ids of the registrations billed.
    """
    from .payments import send_payment_email
    from .tasks import run_later_for

    regs = awaiting_billing(se)
    se.tentative = False
    db.session.commit()

    billed = []
    for reg in regs:
        entries = dict(reg.sub_events or {})
        entries[se.key] = {**entries[se.key], "charged": se.price, "name": se.name}
        reg.sub_events = entries
        db.session.commit()
        reg.charge_to(reg.total_price, reason=f"{se.name} billed")
        reg.settle_status()
        db.session.commit()
        billed.append(reg.id)
        if send_invoices and reg.amount_due > 0 and reg.user and reg.user.email:
            run_later_for(Registration, reg.id, send_payment_email, note=note)
    return billed
