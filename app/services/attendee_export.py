"""Attendee list as a CSV: one row per registration, with the columns an admin
ticks on the registration report page.

Unlike the report itself this carries names, so it is for the committee's
own use (badges, check-in, the dinner list) and every download is audited.

The columns on offer are a few the app works out (registration number,
payment, badge name, an honorific) followed by every question on the
conference's registration form, in form order, and each sub-event. The
choice is saved on the conference, so it is set once and every later
download matches — and anyone on the committee can change it.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from . import registration_report as rr

# Form field types that are not answers of their own: the tier is offered as
# a built-in column, sub-events from the conference's own list.
_NOT_ANSWERS = ("registration-tier", "sub-event")

GROUP_REGISTRATION = "Registration"
GROUP_FORM = "Form answers"
GROUP_SENSITIVE = "Contact and identity details"


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    group: str
    hint: str = ""


# ---------------------------------------------------------------------------
# Which form field is which. Found by key first, then by label, so a form
# built differently still gets sensible suggestions; nothing depends on them.
# ---------------------------------------------------------------------------

def _fields(schema) -> list[dict]:
    return [f for section in (schema or {}).get("sections", [])
            for f in section.get("fields", []) if f.get("key")]


def _find(fields, keys=(), label_words=(), types=None) -> dict | None:
    for f in fields:
        if f["key"] in keys:
            return f
    for f in fields:
        label = (f.get("label") or "").lower()
        if (any(w in label for w in label_words)
                and (types is None or f.get("type") in types)):
            return f
    return None


def _roles(schema) -> dict[str, dict | None]:
    fields = _fields(schema)
    text = ("text",)
    return {
        "given": _find(fields, ("given-name", "first-name"), ("first name", "given name"), text),
        "family": _find(fields, ("family-name", "last-name", "surname"),
                        ("last name", "family name", "surname"), text),
        "preferred": _find(fields, ("moniker", "preferred-name"), ("preferred name",), text),
        "title": _find(fields, ("position-title", "title", "honorific"),
                       ("position / title", "honorific", "title"), text),
        "affiliation": _find(fields, ("affiliation", "organisation", "institution"),
                             ("organisation", "institution", "affiliation", "company"), text),
        "dinner": next((f for f in fields if "dinner" in f["key"].lower()
                        and f.get("type") in ("radio", "select", "checkbox")), None),
    }


def _sensitive(field: dict) -> bool:
    """Contact and identity details: worth a second thought before export."""
    words = f"{field.get('key', '')} {field.get('label', '')}".lower()
    return (field.get("type") in ("email", "tel", "billing-address")
            or "passport" in words or "nationality" in words)


# ---------------------------------------------------------------------------
# Honorific, worked out from what people typed as their title
# ---------------------------------------------------------------------------

_HONORIFICS = (
    (r"(assoc(iate)?\.?\s*prof(essor)?\.?|a/\s*prof\.?)", "A/Prof"),
    (r"(asst\.?|assistant)\s*prof(essor)?\.?", "Asst Prof"),
    (r"((adjunct|emeritus|clinical|honorary|full)\s+)?prof(essor)?\.?", "Prof"),
    (r"(dr|doctor)\.?", "Dr"),
    (r"mrs\.?", "Mrs"),
    (r"mr\.?", "Mr"),
    (r"ms\.?", "Ms"),
    (r"miss", "Miss"),
    (r"mx\.?", "Mx"),
)
_HONORIFIC_RES = [(re.compile(rf"^\s*{p}(?=$|[\s,/(-])", re.IGNORECASE), h)
                  for p, h in _HONORIFICS]


def honorific(title: str | None) -> str:
    """The honorific a title starts with — "Associate Professor" → "A/Prof",
    "Dr." → "Dr", "Professor of Pharmacy" → "Prof" — or "" when it starts
    with none ("PhD student", "Research Fellow"). Never guessed from a job:
    a postdoc is probably a Dr, but probably is not good enough for a badge.
    """
    for rx, h in _HONORIFIC_RES:
        if rx.match(title or ""):
            return h
    return ""


# ---------------------------------------------------------------------------
# The columns on offer, and the suggested ones
# ---------------------------------------------------------------------------

def columns(conference) -> list[Column]:
    schema = conference.registration_form_schema
    roles = _roles(schema)
    out = [Column("reference", "Registration no.", GROUP_REGISTRATION, "REG-000123")]
    if roles["title"]:
        out.append(Column("honorific", "Honorific", GROUP_REGISTRATION,
                          f"from “{roles['title'].get('label')}” — only when it starts "
                          "with Dr, Prof, A/Prof, Mr, Ms…"))
    if roles["given"]:
        out.append(Column("badge_first_name", "Badge first name", GROUP_REGISTRATION,
                          "preferred name if given, otherwise first name"))
    out += [
        Column("tier", "Registration tier", GROUP_REGISTRATION),
        Column("status", "Status", GROUP_REGISTRATION, "paid, pending, refunded…"),
        Column("payment", "Payment", GROUP_REGISTRATION,
               "how it was paid, from the ledger: Worldline, manual, part paid…"),
        Column("owing", "Amount owing", GROUP_REGISTRATION),
        Column("registered", "Registered on", GROUP_REGISTRATION),
    ]
    for f in _fields(schema):
        if f.get("type") in _NOT_ANSWERS:
            continue
        group = GROUP_SENSITIVE if _sensitive(f) else GROUP_FORM
        out.append(Column(f"f:{f['key']}", f.get("label") or f["key"], group))
    for se in conference.sub_events:
        out.append(Column(f"se:{se.id}", se.name, GROUP_FORM, "workshop or other add-on"))
    return out


def suggested(conference) -> list[str]:
    """A badge-and-check-in list: who they are, where from, paid, dinner, diet."""
    roles = _roles(conference.registration_form_schema)
    keys = ["reference", "honorific", "badge_first_name"]
    keys += [f"f:{roles[r]['key']}" for r in ("given", "family", "preferred",
                                             "affiliation", "title") if roles[r]]
    keys += ["tier", "payment"]
    if roles["dinner"]:
        keys.append(f"f:{roles['dinner']['key']}")
    # Diet questions by key, or by a label that is about diet ("Dietary
    # requirements", "Other dietary…") — not one that merely mentions it,
    # like a dinner wait-list warning that dietary needs may not be met.
    keys += [f"f:{f['key']}" for f in _fields(conference.registration_form_schema)
             if "diet" in f["key"].lower()
             or (f.get("label") or "").lower().startswith(("dietary", "other dietary"))]
    offered = {c.key for c in columns(conference)}
    return [k for k in dict.fromkeys(keys) if k in offered]


def saved(conference) -> tuple[list[str], bool]:
    """The saved choice (columns, include refunded/cancelled), or the
    suggestion when nothing is saved yet. Columns no longer on the form drop
    out quietly."""
    data = conference.attendee_export or {}
    offered = {c.key for c in columns(conference)}
    keys = [k for k in (data.get("columns") or []) if k in offered]
    return (keys or suggested(conference)), bool(data.get("include_inactive"))


# ---------------------------------------------------------------------------
# The CSV
# ---------------------------------------------------------------------------

def _text(field: dict, data: dict) -> str:
    """An answer as one cell: blank when the question was never shown."""
    from .form_renderer import _condition_met

    if field.get("condition") and not _condition_met(field["condition"], data):
        return ""
    value = data.get(field["key"])
    if field.get("type") == "checkbox":
        return "Yes" if value else "No"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return " ".join(str(value).split()) if value is not None else ""


def _safe(cell: str) -> str:
    """Stop a spreadsheet reading a typed answer as a formula."""
    return "'" + cell if cell[:1] in ("=", "+", "-", "@", "\t", "\r") else cell


def build_csv(conference, keys: list[str], *, include_inactive: bool = False,
              currency_symbol: str = "$") -> tuple[bytes, int]:
    """(CSV bytes, number of registrations). Sorted by family name."""
    cols = {c.key: c for c in columns(conference)}
    keys = [k for k in keys if k in cols]
    fields = {f["key"]: f for f in _fields(conference.registration_form_schema)}
    roles = _roles(conference.registration_form_schema)
    sub_events = {f"se:{se.id}": se for se in conference.sub_events}
    labels = dict(rr.SETTLEMENTS)

    regs = [r for r in rr._registrations(conference)
            if include_inactive or r.status not in rr._INACTIVE]

    def answer(reg, role):
        f = roles[role]
        return _text(f, reg.custom_data or {}) if f else ""

    def sort_key(reg):
        name = (answer(reg, "family") or reg.user.full_name or "").casefold()
        return (name, answer(reg, "given").casefold(), reg.id)

    regs.sort(key=sort_key)
    moves = rr._movements(regs) if "payment" in keys else {}

    def cell(reg, key) -> str:
        data = reg.custom_data or {}
        if key == "reference":
            return reg.reference
        if key == "honorific":
            return honorific(answer(reg, "title"))
        if key == "badge_first_name":
            return answer(reg, "preferred") or answer(reg, "given")
        if key == "tier":
            return reg.tier_name or ""
        if key == "status":
            return reg.status
        if key == "payment":
            return labels.get(rr.settlement(reg, moves[reg.id]), "")
        if key == "owing":
            due = 0 if reg.status in rr._INACTIVE else reg.amount_due
            return f"{currency_symbol}{due / 100:,.2f}" if due else ""
        if key == "registered":
            return reg.created_at.strftime("%Y-%m-%d") if reg.created_at else ""
        if key in sub_events:
            entry = (reg.sub_events or {}).get(sub_events[key].key)
            if not isinstance(entry, dict):
                return ""
            return "Attending" if entry.get("attending") else "Not attending"
        if key.startswith("f:"):
            return _text(fields[key[2:]], data)
        return ""

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([cols[k].label for k in keys])
    for reg in regs:
        w.writerow([_safe(cell(reg, k)) for k in keys])
    # A byte-order mark, so Excel opens accented names as UTF-8.
    return ("﻿" + buf.getvalue()).encode("utf-8"), len(regs)


def filename(conference) -> str:
    from datetime import datetime
    return f"attendees-{conference.slug}-{datetime.utcnow():%Y%m%d-%H%M}.csv"
