"""Attendance limits on registration form items.

An item is limited by ticking "Limited" in the form builder, which puts these
on its definition in the registration form schema:

  max_attendees   how many registrations may take a seat (absent = no limit)
  min_attendees   how many it needs to go ahead (absent = none); shown to
                  admins, never enforced on a registrant
  counts_option   for radio / select / checkbox-group, the answer that takes
                  a seat ("Yes" on an attending-dinner question)
  full_value      for radio / select, what is recorded instead once the item
                  is full (e.g. "at-capacity"). Never one of the options, so it
                  cannot be chosen while seats remain; other fields can be made
                  conditional on it, to ask for an expression of interest.

An item that is not limited carries none of these and behaves exactly as it
did before they existed. They live in the schema JSON beside `required` and
`options`, so there is nothing to migrate.

Seats are counted from the registrations themselves on every read rather than
kept in a counter: a counter would have to be corrected on every edit, refund,
cancellation and deletion, and would drift the first time one was missed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from ..models import Registration

# The field types where an answer can mean "I am coming".
LIMITED_TYPES = ("checkbox", "radio", "select", "checkbox-group", "sub-event")
OPTION_TYPES = ("radio", "select", "checkbox-group")
# Single-answer fields, where a full item can record a value of its own.
FULL_VALUE_TYPES = ("radio", "select")
LIMIT_KEYS = ("max_attendees", "min_attendees", "counts_option", "full_value")

# A registration in one of these states has given its seat back.
_NO_SEAT = ("cancelled", "refunded")


@dataclass
class Seats:
    label: str
    max: int | None
    min: int | None
    taken: int              # seats held, the viewer's own included
    holds: bool             # the viewer already has one
    option: str | None      # the answer that takes a seat, for option fields
    full_value: str | None  # recorded instead once full, for radio / select

    @property
    def left(self) -> int | None:
        return None if self.max is None else max(0, self.max - self.taken)

    @property
    def full(self) -> bool:
        """No seat for this viewer. Someone who holds one keeps it, so an
        item at its limit is never full for the people already in it."""
        return self.max is not None and not self.holds and self.taken >= self.max

    @property
    def short(self) -> int:
        """How many more it needs to reach its minimum."""
        return max(0, (self.min or 0) - self.taken)


def parse_limit(raw) -> int | None:
    """A limit as typed into the form builder: blank means none."""
    raw = (raw or "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 0:
        raise ValueError("An attendee limit cannot be negative.")
    return value


def default_option(options: list[str]) -> str | None:
    """The answer that takes a seat when the admin names none: "Yes" if the
    field offers it, otherwise its first option."""
    for o in options:
        if o.strip().lower() == "yes":
            return o
    return options[0] if options else None


def is_limited(field: dict) -> bool:
    return (field.get("type") in LIMITED_TYPES
            and (field.get("max_attendees") is not None
                 or field.get("min_attendees") is not None))


def _fields(schema) -> list[dict]:
    return [f for section in (schema or {}).get("sections", [])
            for f in section.get("fields", []) if is_limited(f)]


def _takes_seat(field: dict, custom_data, sub_event_answers, events) -> bool:
    """Whether these answers take one of *field*'s seats."""
    ftype = field.get("type")
    if ftype == "sub-event":
        se = events.get(field.get("sub_event_id"))
        entry = (sub_event_answers or {}).get(se.key) if se else None
        return bool(isinstance(entry, dict) and entry.get("attending"))
    value = (custom_data or {}).get(field.get("key", ""))
    if ftype == "checkbox":
        return bool(value)
    option = field.get("counts_option")
    if not option:
        return False
    if isinstance(value, list):
        return option in value
    return value == option


def status(conference_id: int, schema, sub_events, own=None) -> dict[str, Seats]:
    """Seats for every limited field of the form, by field key.

    *own* is the viewer's registration, if they have one: it says whether
    they already hold a seat, which is what lets them keep it when the item
    is full.
    """
    fields = _fields(schema)
    if not fields:
        return {}
    events = {se.id: se for se in sub_events}
    regs = (Registration.query
            .filter(Registration.conference_id == conference_id,
                    Registration.deleted_at.is_(None),
                    Registration.status.notin_(_NO_SEAT))
            .all())
    own_id = own.id if own is not None else None
    out = {}
    for field in fields:
        taken, holds = 0, False
        for reg in regs:
            if _takes_seat(field, reg.custom_data, reg.sub_events, events):
                taken += 1
                holds = holds or reg.id == own_id
        ftype = field.get("type")
        out[field.get("key", "")] = Seats(
            label=field.get("label") or field.get("key", ""),
            max=field.get("max_attendees"), min=field.get("min_attendees"),
            taken=taken, holds=holds,
            option=field.get("counts_option") if ftype in OPTION_TYPES else None,
            full_value=field.get("full_value") if ftype in FULL_VALUE_TYPES else None)
    return out


def apply_full_values(seats: dict[str, Seats], schema, form):
    """The posted form with each full item's answer set to its full value.

    The registration form already posts the full value for a full item; this
    covers a form opened before the item filled, which still offers the seat.
    Returns (form, labels of the items whose answer was replaced). An answer
    that takes no seat ("No") is left as it was.
    """
    replaced = []
    out = None
    for field in _fields(schema):
        s = seats.get(field.get("key", ""))
        if s is None or not s.full or not s.full_value:
            continue
        key = field["key"]
        posted = (form.get(key) or "").strip()
        if posted == s.full_value or (posted and posted != s.option):
            continue
        if out is None:
            out = form.copy()
        out[key] = s.full_value
        if posted:
            replaced.append(s.label)
    return (out if out is not None else form), replaced


def validation_schema(schema, seats: dict[str, Seats]):
    """*schema* with each full item's full value accepted as an answer.

    Only while the item is full: the rest of the time the full value is not
    one of its options, and validation refuses it like any other stranger.
    """
    full = {k: s.full_value for k, s in seats.items() if s.full and s.full_value}
    if not full:
        return schema
    schema = copy.deepcopy(schema)
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            if field.get("key") in full:
                field["options"] = [*field.get("options", []), full[field["key"]]]
    return schema


def refusals(seats: dict[str, Seats], schema, custom_data, sub_event_answers,
             sub_events) -> list[str]:
    """An error for each full item the answers still ask for a seat in.

    Items with a full value never get here holding a seat — their answer was
    replaced — so this is the backstop for the ones without.
    """
    events = {se.id: se for se in sub_events}
    errors = []
    for field in _fields(schema):
        s = seats.get(field.get("key", ""))
        if s is not None and s.full and _takes_seat(
                field, custom_data, sub_event_answers, events):
            errors.append(f"{s.label} is full — all {s.max} seats are taken.")
    return errors
