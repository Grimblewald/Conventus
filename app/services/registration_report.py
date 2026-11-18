"""A conference's registration report: counts, breakdowns, and a Sankey.

One computation behind both the admin report page and its PDF, so the two can
never disagree. Every figure is counted from the registrations themselves at
the moment the report is asked for; nothing here writes.

Nothing that identifies a registrant leaves this module, so a report can be
passed around a committee without becoming a list of people. Free text stays
out too, with one exception: what people typed where a question's "Other"
asked them to specify (a dietary need, say), which the organisers have to act
on. Those are listed in an appendix, grouped and counted, never beside who
wrote them.

The Sankey is laid out once, in abstract units, and drawn twice: as SVG for
the page and as TikZ for the PDF.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import Registration
from . import capacity

# The order statuses are listed and stacked in. Anything else found is
# appended after these, so an unexpected status is shown, not dropped.
STATUS_ORDER = ("paid", "pending", "failed", "refunded", "cancelled")
# Registrations in these states hold no place and owe nothing.
_INACTIVE = ("refunded", "cancelled")

# Field types whose answers are a choice, and so can be counted.
_CHOICE_TYPES = ("radio", "select", "checkbox", "checkbox-group", "country")
NO_ANSWER = "(no answer)"
NOT_ASKED = "(not asked)"
_UNANSWERED = (NO_ANSWER, NOT_ASKED)
_OTHER = "Other"
# A breakdown with more answers than this folds the smallest into "Other"
# (countries, mostly); a Sankey column folds past the eight categorical hues.
_MAX_ROWS = 15
_MAX_NODES = 8

# The dataviz reference palette (light mode), in its fixed, CVD-validated
# order. Hues are assigned by an answer's position in its field's option
# list, never by its rank, so a colour means the same answer on every run.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#6250d6", "#e34948")
# Status colours are reserved for state, and always shown with their label.
STATUS_COLOURS = {"paid": "#0ca30c", "pending": "#fab219", "failed": "#d03b3b",
                  "refunded": "#ec835a", "cancelled": "#898781"}
NEUTRAL = "#898781"


# ---------------------------------------------------------------------------
# Counts and breakdowns
# ---------------------------------------------------------------------------

@dataclass
class Breakdown:
    key: str
    label: str
    multi: bool                        # people could pick more than one
    rows: list[tuple[str, Counter]]    # (answer, count by status)

    def total(self, counts: Counter) -> int:
        return sum(counts.values())


@dataclass
class OtherAnswers:
    """What was typed into the follow-up of a question's "Other" answer."""
    question: str                      # the choice question
    detail: str                        # the follow-up field's own label
    answers: list[tuple[str, int]]     # (answer, how many gave it)
    blank: int                         # chose Other, left the follow-up empty

    @property
    def total(self) -> int:
        return sum(n for _, n in self.answers) + self.blank


@dataclass
class Report:
    conference: object
    generated_at: datetime
    statuses: list[str]                # the status columns, in order
    total: int                         # registrations, deleted excluded
    deleted: int
    by_status: Counter
    paid_with_fee: int
    paid_no_fee: int
    paid_unrecorded: int               # marked paid, fee due, nothing received
    received: int                      # cents, net of refunds
    outstanding: int                   # cents still owed, refunded/cancelled aside
    owing: Counter                     # registrations owing something, by status
    tiers: Breakdown
    sub_events: list[Breakdown]
    fields: list[Breakdown]
    seats: dict                        # field key -> capacity.Seats
    other_answers: list[OtherAnswers] = field(default_factory=list)

    @property
    def active(self) -> int:
        return self.total - sum(self.by_status[s] for s in _INACTIVE)

    @property
    def paid_note(self) -> str:
        note = f"{self.paid_with_fee} with a fee, {self.paid_no_fee} with no fee"
        if self.paid_unrecorded:
            note += (f"; {self.paid_unrecorded} marked paid with no payment "
                     f"recorded")
        return note

    @property
    def owing_note(self) -> str:
        """Who the outstanding amount is owed by, e.g. "26 registrations
        (21 pending, 5 paid with a balance)". A paid registration owing
        something is worth seeing: a charge was added after it paid."""
        n = sum(self.owing.values())
        if not n:
            return "nothing owed"
        parts = [f"{c} {s}" + (" with a balance" if s == "paid" else "")
                 for s in self.statuses for c in [self.owing.get(s, 0)] if c]
        return f"owed by {n} registration{'s' if n != 1 else ''} ({', '.join(parts)})"


def _status_order(found) -> list[str]:
    return [s for s in STATUS_ORDER if s in found] + sorted(set(found) - set(STATUS_ORDER))


def _registrations(conference):
    return (Registration.query
            .filter(Registration.conference_id == conference.id,
                    Registration.deleted_at.is_(None))
            .order_by(Registration.id)
            .all())


def choice_fields(schema) -> list[dict]:
    """The registration form's fields whose answers can be counted."""
    return [f for section in (schema or {}).get("sections", [])
            for f in section.get("fields", []) if f.get("type") in _CHOICE_TYPES]


def _answer(field: dict, data: dict):
    """A registration's answer to *field*, as one label or a list of them.

    A question shown only on a condition (a wait-list offered once the dinner
    is full) reads "(not asked)" for everyone who never saw it, rather than a
    "No" they never gave.
    """
    from .form_renderer import _condition_met

    if field.get("condition") and not _condition_met(field["condition"], data or {}):
        return NOT_ASKED
    value = (data or {}).get(field.get("key", ""))
    if field.get("type") == "checkbox":
        return "Yes" if value else "No"
    if isinstance(value, list):
        return [v for v in value if v] or [NO_ANSWER]
    return value.strip() if isinstance(value, str) and value.strip() else NO_ANSWER


def _ordered(counts: dict, preferred: list[str]) -> list[str]:
    """Answers in the field's own option order, then any others by count,
    with no answer and not asked last."""
    known = [o for o in preferred if o in counts]
    rest = sorted((a for a in counts if a not in preferred and a not in _UNANSWERED),
                  key=lambda a: (-sum(counts[a].values()), a))
    tail = [a for a in _UNANSWERED if a in counts]
    return known + rest + tail


def _fold(rows: list[tuple[str, Counter]], limit: int) -> list[tuple[str, Counter]]:
    """Keep the *limit* - 1 largest answers and fold the rest into Other,
    preserving the order of those kept."""
    if len(rows) <= limit:
        return rows
    keep = {a for a, _ in sorted(rows, key=lambda r: -sum(r[1].values()))[:limit - 1]}
    other = Counter()
    for answer, counts in rows:
        if answer not in keep:
            other.update(counts)
    return [r for r in rows if r[0] in keep] + [(_OTHER, other)]


def _breakdown(key, label, regs, value_of, preferred=(), multi=False) -> Breakdown:
    counts: dict[str, Counter] = {}
    for reg in regs:
        values = value_of(reg)
        for v in (values if isinstance(values, list) else [values]):
            counts.setdefault(v, Counter())[reg.status] += 1
    rows = [(a, counts[a]) for a in _ordered(counts, list(preferred))]
    return Breakdown(key, label, multi, _fold(rows, _MAX_ROWS))


def _other_followups(schema) -> list[tuple[dict, dict]]:
    """(choice field, text field) pairs where the text field only appears
    when the choice's answer includes "Other" — the "please specify" box.

    Matched on the condition, not on key names, so it works however the
    form builder named them. Other free-text fields (an abstract title, a
    note) are deliberately not matched, and never reach the report.
    """
    choices = {f["key"]: f for f in choice_fields(schema)}
    pairs = []
    for section in (schema or {}).get("sections", []):
        for f in section.get("fields", []):
            cond = f.get("condition") or {}
            if (f.get("type") in ("text", "textarea")
                    and cond.get("field") in choices
                    and str(cond.get("contains", "")).strip().lower() == "other"):
                pairs.append((choices[cond["field"]], f))
    return pairs


def _shown(detail: dict, data: dict) -> bool:
    """Whether the follow-up was on screen for these answers — the same test
    the registration form applies (form_renderer._condition_met)."""
    from .form_renderer import _condition_met
    return _condition_met(detail["condition"], data)


def _other_answers(regs, schema) -> list[OtherAnswers]:
    out = []
    for choice, detail in _other_followups(schema):
        seen: dict[str, str] = {}           # case-folded -> first spelling
        counts: Counter = Counter()
        blank = 0
        for reg in regs:
            data = reg.custom_data or {}
            if not _shown(detail, data):
                continue                     # the follow-up was never shown
            text = " ".join(str(data.get(detail["key"]) or "").split())
            if not text:
                blank += 1
                continue
            key = text.casefold()
            seen.setdefault(key, text)
            counts[key] += 1
        answers = sorted(((seen[k], n) for k, n in counts.items()),
                         key=lambda a: (-a[1], a[0].casefold()))
        if answers or blank:
            out.append(OtherAnswers(choice.get("label") or choice["key"],
                                    detail.get("label") or detail["key"],
                                    answers, blank))
    return out


def _sub_event_answer(se):
    def value_of(reg):
        entry = (reg.sub_events or {}).get(se.key)
        if not isinstance(entry, dict):
            return NOT_ASKED
        return "Attending" if entry.get("attending") else "Not attending"
    return value_of


def build(conference) -> Report:
    from ..models.payment_event import amount_received

    regs = _registrations(conference)
    deleted = (Registration.query
               .filter(Registration.conference_id == conference.id,
                       Registration.deleted_at.isnot(None))
               .count())
    schema = conference.registration_form_schema
    by_status = Counter(r.status for r in regs)

    paid = [r for r in regs if r.status == "paid"]
    received = {r.id: amount_received(r.id) for r in regs}
    owing = [r for r in regs if r.status not in _INACTIVE and r.amount_due > 0]

    tiers = _breakdown("tier", "Registration tier", regs, lambda r: r.tier_name,
                       preferred=[t.name for t in conference.price_tiers])
    sub_events = [
        _breakdown(f"se:{se.id}", se.name, regs, _sub_event_answer(se),
                   preferred=["Attending", "Not attending"])
        for se in conference.sub_events]
    fields = [
        _breakdown(f"f:{f['key']}", f.get("label") or f["key"], regs,
                   lambda r, f=f: _answer(f, r.custom_data),
                   preferred=(["Yes", "No"] if f.get("type") == "checkbox"
                              else list(f.get("options") or [])),
                   multi=f.get("type") == "checkbox-group")
        for f in choice_fields(schema)]

    return Report(
        conference=conference,
        generated_at=datetime.now(timezone.utc),
        statuses=_status_order(by_status),
        total=len(regs), deleted=deleted, by_status=by_status,
        paid_with_fee=sum(1 for r in paid if r.total_price > 0),
        paid_no_fee=sum(1 for r in paid if r.total_price <= 0),
        paid_unrecorded=sum(1 for r in paid if r.total_price > 0 and received[r.id] <= 0),
        received=sum(received.values()),
        outstanding=sum(r.amount_due for r in owing),
        owing=Counter(r.status for r in owing),
        tiers=tiers, sub_events=sub_events, fields=fields,
        seats=capacity.status(conference.id, schema, conference.sub_events),
        other_answers=_other_answers(regs, schema),
    )


# ---------------------------------------------------------------------------
# Sankey
# ---------------------------------------------------------------------------

def stage_options(conference) -> list[tuple[str, str]]:
    """(key, label) for everything a Sankey column can split on: one answer
    per registration, so each column accounts for everyone exactly once."""
    out = [("status", "Payment status"), ("tier", "Registration tier")]
    out += [(f"se:{se.id}", se.name) for se in conference.sub_events]
    out += [(f"f:{f['key']}", f.get("label") or f["key"])
            for f in choice_fields(conference.registration_form_schema)
            if f.get("type") != "checkbox-group"]
    return out


def default_stages(conference) -> list[str]:
    """Status and tier, then the first limited field, else the first
    sub-event — the cohorts an organiser most often has to count."""
    stages = ["status", "tier"]
    schema = conference.registration_form_schema
    limited = [f for s in (schema or {}).get("sections", [])
               for f in s.get("fields", [])
               if capacity.is_limited(f) and f.get("type") != "checkbox-group"]
    if limited:
        key = (f"se:{limited[0]['sub_event_id']}" if limited[0]["type"] == "sub-event"
               else f"f:{limited[0]['key']}")
        stages.append(key)
    elif conference.sub_events:
        stages.append(f"se:{conference.sub_events[0].id}")
    return stages


@dataclass
class Node:
    stage: int
    label: str
    count: int
    colour: str
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0


@dataclass
class Link:
    source: Node
    target: Node
    count: int
    sy: float = 0.0      # top of the band where it leaves the source
    ty: float = 0.0      # top of the band where it reaches the target
    h: float = 0.0

    @property
    def share(self) -> float:
        """The fraction of its source this band carries."""
        return self.count / self.source.count if self.source.count else 0.0


@dataclass
class Sankey:
    stages: list[tuple[str, str]]          # (key, label), the root first
    columns: list[list[Node]]
    links: list[Link]
    width: float
    height: float
    node_width: float
    total: int
    flows: list[tuple[str, str, str, str, int, float]] = field(default_factory=list)


def _stage_value(conference, key: str):
    """(value_of(reg), preferred order, colour for a value) for a stage."""
    if key == "status":
        return (lambda r: r.status.title(),
                [s.title() for s in STATUS_ORDER],
                lambda v, i: STATUS_COLOURS.get(v.lower(), NEUTRAL))
    if key == "tier":
        return (lambda r: r.tier_name, [t.name for t in conference.price_tiers], None)
    if key.startswith("se:"):
        se = next((s for s in conference.sub_events if str(s.id) == key[3:]), None)
        if se is None:
            raise KeyError(key)
        return _sub_event_answer(se), ["Attending", "Not attending"], None
    if key.startswith("f:"):
        f = next((f for f in choice_fields(conference.registration_form_schema)
                  if f["key"] == key[2:] and f.get("type") != "checkbox-group"), None)
        if f is None:
            raise KeyError(key)
        preferred = ["Yes", "No"] if f.get("type") == "checkbox" else list(f.get("options") or [])
        return (lambda r: _answer(f, r.custom_data), preferred, None)
    raise KeyError(key)


def _colour(value: str, preferred: list[str], index: int, fixed) -> str:
    if value in (*_UNANSWERED, _OTHER):
        return NEUTRAL
    if fixed is not None:
        return fixed(value, index)
    slot = preferred.index(value) if value in preferred else index
    return SERIES[slot % len(SERIES)]


def build_sankey(conference, stage_keys: list[str], *, width: float = 760,
                 height: float = 440, node_width: float = 12,
                 gap: float = 12) -> Sankey:
    """Lay out a Sankey of every registration through *stage_keys*.

    Each column partitions all registrations (deleted excluded), so every
    column sums to the total. A band between neighbouring columns carries the
    registrations in both its source and its target; its share is of the
    source. Positions are abstract units, (0, 0) top left.
    """
    regs = _registrations(conference)
    labels = dict(stage_options(conference))
    stages = [("all", "All registrations")] + [(k, labels[k]) for k in stage_keys]

    # Each registration's path through the columns.
    paths = [["All registrations"] for _ in regs]
    columns: list[list[Node]] = [[Node(0, "All registrations", len(regs), NEUTRAL)]]
    for si, key in enumerate(stage_keys, start=1):
        value_of, preferred, fixed = _stage_value(conference, key)
        values = [value_of(r) for r in regs]
        counts = Counter(values)
        order = _ordered({v: Counter({"": c}) for v, c in counts.items()}, preferred)
        if len(order) > _MAX_NODES:
            keep = set(sorted(order, key=lambda v: -counts[v])[:_MAX_NODES - 1])
            values = [v if v in keep else _OTHER for v in values]
            counts = Counter(values)
            order = [v for v in order if v in keep] + [_OTHER]
        for path, v in zip(paths, values):
            path.append(v)
        columns.append([Node(si, v, counts[v], _colour(v, preferred, i, fixed))
                        for i, v in enumerate(order)])

    total = len(regs)
    # Vertical scale: the busiest column, gaps included, fills the height.
    most = max(len(c) for c in columns)
    scale = (height - gap * (most - 1)) / total if total else 0
    step = (width - node_width) / (len(columns) - 1) if len(columns) > 1 else 0
    for ci, col in enumerate(columns):
        span = sum(n.count for n in col) * scale + gap * (len(col) - 1)
        y = (height - span) / 2
        for n in col:
            n.x, n.y, n.h = ci * step, y, n.count * scale
            y += n.h + gap

    links: list[Link] = []
    for ci in range(len(columns) - 1):
        by_label = {n.label: n for n in columns[ci]}
        nxt = {n.label: n for n in columns[ci + 1]}
        pairs = Counter((p[ci], p[ci + 1]) for p in paths)
        links += [Link(by_label[s], nxt[t], c) for (s, t), c in pairs.items()]

    # Stack bands out of each source in target order, and into each target in
    # source order, so no two bands cross at a node.
    rank = {id(n): i for col in columns for i, n in enumerate(col)}
    for n in (n for col in columns for n in col):
        y = n.y
        for link in sorted((lk for lk in links if lk.source is n),
                           key=lambda lk: rank[id(lk.target)]):
            link.sy, link.h = y, link.count * scale
            y += link.h
        y = n.y
        for link in sorted((lk for lk in links if lk.target is n),
                           key=lambda lk: rank[id(lk.source)]):
            link.ty = y
            y += link.count * scale

    flows = [(stages[lk.source.stage][1], stages[lk.target.stage][1],
              lk.source.label, lk.target.label, lk.count, lk.share)
             for lk in sorted(links, key=lambda lk: (lk.source.stage, rank[id(lk.source)],
                                                     rank[id(lk.target)]))]
    return Sankey(stages, columns, links, width, height, node_width, total, flows)


def parse_stages(conference, raw: list[str]) -> list[str]:
    """The stage keys asked for, keeping only real ones, in order, once each;
    the defaults when none survive."""
    valid = dict(stage_options(conference))
    picked = []
    for k in raw:
        if k in valid and k not in picked:
            picked.append(k)
    return picked[:4] or default_stages(conference)


def percent(share: float) -> str:
    """A share as a percentage that never rounds a real band to 0% or 100%."""
    p = share * 100
    if 0 < p < 0.5:
        return "<1%"
    if 99.5 <= p < 100:
        return ">99%"
    return f"{p:.0f}%"


# ---------------------------------------------------------------------------
# Drawing: SVG for the page, TikZ for the PDF
# ---------------------------------------------------------------------------

_MIN_BAND_LABEL = 13    # units: thinner bands carry their share on hover only
_MAX_TITLE = 28         # characters: a column title is cut to fit between columns


def _stage_title(sk: Sankey, ci: int) -> tuple[str, float, str]:
    """(title, x, anchor) for column *ci*: the first left-aligned to its
    nodes, the last right-aligned, the rest centred, so neighbours never
    run into each other. Long titles (a whole question) are shortened; the
    breakdown tables carry them in full."""
    title = sk.stages[ci][1]
    if len(title) > _MAX_TITLE:
        title = title[:_MAX_TITLE - 1].rstrip(" ,(—-") + "…"
    x = sk.columns[ci][0].x
    if ci == 0:
        # The first column is one box: its count goes up here, because a
        # label beside it would sit on the percentages of the bands leaving.
        return f"{title} · {sk.total}", x, "start"
    if ci == len(sk.stages) - 1:
        return title, x + sk.node_width, "end"
    return title, x + sk.node_width / 2, "middle"


def _band_label_pos(lk: Link, sk: Sankey) -> tuple[float, float]:
    """A quarter of the way along the band from its source: close to the box
    it is a share of, and clear of the middle, where bands cross.

    The band's edges are cubics with both control points at mid-x, so a
    quarter of the way across is at t = 0.2, where the curve has moved
    0.104 of the way from the source's height to the target's."""
    x0 = lk.source.x + sk.node_width
    x1 = lk.target.x
    return x0 + (x1 - x0) * 0.25, lk.sy + (lk.ty - lk.sy) * 0.104 + lk.h / 2


def svg(sk: Sankey, *, label_room: float = 150) -> str:
    """The Sankey as an inline SVG; the page's CSS gives text its colours.

    Bands and boxes carry `data-tip` for the page's hover layer. The page's
    table of every band is the view for anyone without a pointer or script.
    """
    from markupsafe import escape

    pad_l, pad_r, pad_t = 4, label_room, 30
    w, h = sk.width + pad_l + pad_r, sk.height + pad_t + 8
    out = [f'<svg class="sankey" viewBox="0 0 {w:.0f} {h:.0f}" role="img" '
           f'aria-label="Registrations split by {escape(", ".join(s[1] for s in sk.stages[1:]))}">']
    out.append(f'<g transform="translate({pad_l},{pad_t})">')
    for ci in range(len(sk.stages)):
        title, tx, anchor = _stage_title(sk, ci)
        # The full wording on hover, only where it had to be shortened.
        full = sk.stages[ci][1]
        hint = f"<title>{escape(full)}</title>" if title.endswith("…") else ""
        out.append(f'<text class="sk-stage" x="{tx:.1f}" y="-14" text-anchor="{anchor}">'
                   f'{hint}{escape(title)}</text>')
    for lk in sk.links:
        x0, x1 = lk.source.x + sk.node_width, lk.target.x
        xm = (x0 + x1) / 2
        d = (f"M{x0:.1f},{lk.sy:.1f} C{xm:.1f},{lk.sy:.1f} {xm:.1f},{lk.ty:.1f} {x1:.1f},{lk.ty:.1f} "
             f"L{x1:.1f},{lk.ty + lk.h:.1f} C{xm:.1f},{lk.ty + lk.h:.1f} {xm:.1f},{lk.sy + lk.h:.1f} "
             f"{x0:.1f},{lk.sy + lk.h:.1f} Z")
        tip = (f"{lk.source.label} → {lk.target.label}: {lk.count} "
               f"({percent(lk.share)} of {lk.source.label})")
        out.append(f'<path class="sk-band" d="{d}" fill="{lk.target.colour}" '
                   f'data-tip="{escape(tip)}"></path>')
    for lk in sk.links:
        if lk.h >= _MIN_BAND_LABEL:
            x, y = _band_label_pos(lk, sk)
            out.append(f'<text class="sk-share" x="{x:.1f}" y="{y + 4:.1f}" '
                       f'text-anchor="middle">{percent(lk.share)}</text>')
    last = len(sk.columns) - 1
    for col in sk.columns:
        for n in col:
            tip = f"{n.label}: {n.count} ({percent(n.count / sk.total if sk.total else 0)} of all)"
            out.append(f'<rect class="sk-node" x="{n.x:.1f}" y="{n.y:.1f}" '
                       f'width="{sk.node_width}" height="{max(n.h, 1):.1f}" rx="2" '
                       f'fill="{n.colour}" data-tip="{escape(tip)}"></rect>')
            if n.stage == 0:
                continue                    # its count is in the column title
            # Right of the last column, left of the ones between, so a label
            # reads against the bands arriving rather than those leaving.
            right = n.stage == last
            lx = n.x + sk.node_width + 6 if right else n.x - 6
            anchor = "start" if right else "end"
            out.append(f'<text class="sk-label" x="{lx:.1f}" y="{n.y + n.h / 2 + 4:.1f}" '
                       f'text-anchor="{anchor}"><tspan class="sk-name">{escape(n.label)}</tspan> '
                       f'<tspan class="sk-count">{n.count}</tspan></text>')
    out.append("</g></svg>")
    return "\n".join(out)


def tikz(sk: Sankey) -> str:
    """The Sankey as a tikzpicture. Units are points; y is flipped for TikZ."""
    from .documents import latex_escape

    def y(v: float) -> float:
        return sk.height - v

    colours = {}
    for n in (n for col in sk.columns for n in col):
        colours.setdefault(n.colour, f"skc{len(colours)}")
    out = [rf"\definecolor{{{name}}}{{HTML}}{{{hexv[1:].upper()}}}"
           for hexv, name in colours.items()]
    out.append(r"\begin{tikzpicture}[x=1pt,y=1pt,font=\sffamily\scriptsize]")
    tikz_anchor = {"start": "south west", "middle": "south", "end": "south east"}
    for ci in range(len(sk.stages)):
        title, tx, anchor = _stage_title(sk, ci)
        out.append(rf"\node[anchor={tikz_anchor[anchor]},inner sep=0,text=inkmuted] "
                   rf"at ({tx:.1f},{y(-8):.1f}) {{\bfseries {latex_escape(title)}}};")
    for lk in sk.links:
        x0, x1 = lk.source.x + sk.node_width, lk.target.x
        xm = (x0 + x1) / 2
        c = colours[lk.target.colour]
        out.append(
            rf"\fill[{c},fill opacity=0.38] ({x0:.1f},{y(lk.sy):.1f}) .. controls "
            rf"({xm:.1f},{y(lk.sy):.1f}) and ({xm:.1f},{y(lk.ty):.1f}) .. ({x1:.1f},{y(lk.ty):.1f}) "
            rf"-- ({x1:.1f},{y(lk.ty + lk.h):.1f}) .. controls ({xm:.1f},{y(lk.ty + lk.h):.1f}) "
            rf"and ({xm:.1f},{y(lk.sy + lk.h):.1f}) .. ({x0:.1f},{y(lk.sy + lk.h):.1f}) -- cycle;")
    for lk in sk.links:
        if lk.h >= _MIN_BAND_LABEL:
            x, yy = _band_label_pos(lk, sk)
            out.append(rf"\node[inner sep=1pt,text=ink] at ({x:.1f},{y(yy):.1f}) "
                       rf"{{{latex_escape(percent(lk.share))}}};")
    last = len(sk.columns) - 1
    for col in sk.columns:
        for n in col:
            c = colours[n.colour]
            out.append(rf"\fill[{c},rounded corners=1pt] ({n.x:.1f},{y(n.y):.1f}) "
                       rf"rectangle ({n.x + sk.node_width:.1f},{y(n.y + max(n.h, 1)):.1f});")
            if n.stage == 0:
                continue                    # its count is in the column title
            right = n.stage == last
            lx = n.x + sk.node_width + 3 if right else n.x - 3
            anchor = "west" if right else "east"
            out.append(rf"\node[anchor={anchor},inner sep=1.5pt,fill=white,fill opacity=0.8,"
                       rf"text opacity=1,rounded corners=1pt] at ({lx:.1f},{y(n.y + n.h / 2):.1f}) "
                       rf"{{{latex_escape(n.label)} \textbf{{{n.count}}}}};")
    out.append(r"\end{tikzpicture}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _money(cents: int, symbol: str) -> str:
    return f"{symbol}{cents / 100:,.2f}"


def _pct(n: int, of: int) -> str:
    return percent(n / of) if of else "–"


def _table_tex(b: Breakdown, statuses: list[str], total: int) -> str:
    from .documents import latex_escape as esc

    cols = "l" + "r" * (len(statuses) + 2)
    head = " & ".join([r"\textbf{Answer}"] + [rf"\textbf{{{esc(s.title())}}}" for s in statuses]
                      + [r"\textbf{Total}", r"\textbf{\%}"])
    lines = [rf"\begin{{tabular}}{{{cols}}}", r"\toprule", head + r" \\", r"\midrule"]
    for answer, counts in b.rows:
        n = sum(counts.values())
        cells = [esc(answer)] + [str(counts.get(s, 0) or "") for s in statuses] + [
            rf"\textbf{{{n}}}", esc(_pct(n, total))]
        lines.append(" & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def _other_answers_tex(groups: list[OtherAnswers]) -> str:
    """The appendix: each question's "Other" follow-ups, grouped and counted.

    A longtable per question, so a long list breaks across pages with its
    header repeated, and answers wrap rather than run off the page."""
    from .documents import latex_escape as esc

    out = [r"\clearpage{\Large\bfseries Appendix: ``Other'' answers\par}",
           r"{\color{inkmuted}\footnotesize What registrants typed when they chose "
           r"``Other'', grouped regardless of capitalisation and counted. Who gave each "
           r"answer is not shown.\par}"]
    for g in groups:
        head = (r"\textbf{Answer} & \textbf{Count} \\ \midrule")
        out.append(r"\par\vspace{10pt}"
                   rf"{{\bfseries {esc(g.question)}}}\par"
                   rf"{{\color{{inkmuted}}\footnotesize {esc(g.detail)} · {g.total} chose "
                   rf"Other\par}}\vspace{{-4pt}}")
        out.append(r"\begin{longtable}{@{}p{0.82\linewidth} r@{}}\toprule " + head
                   + r"\endfirsthead\toprule " + head + r"\endhead")
        for text, n in g.answers:
            out.append(rf"{esc(text)} & {n} \\")
        if g.blank:
            out.append(rf"{{\color{{inkmuted}}\itshape (left blank)}} & {g.blank} \\")
        out.append(r"\bottomrule\end{longtable}")
    return "\n".join(out)


def latex(report: Report, sk: Sankey, *, currency_symbol: str = "$") -> str:
    from .documents import latex_escape as esc

    c = report.conference
    r = report
    summary = [
        ("Registrations", f"{r.total}", "deleted ones excluded"
         + (f" ({r.deleted} deleted)" if r.deleted else "")),
        ("Paid", f"{r.by_status['paid']}", r.paid_note),
        ("Pending", f"{r.by_status['pending']}", "registered, not yet paid in full"),
        ("Refunded / cancelled",
         f"{r.by_status['refunded'] + r.by_status['cancelled']}", "hold no place"),
        ("Received", _money(r.received, currency_symbol), "net of refunds"),
        ("Outstanding", _money(r.outstanding, currency_symbol), r.owing_note),
    ]
    rows = "\n".join(rf"{esc(k)} & \textbf{{{esc(v)}}} & {{\color{{inkmuted}}{esc(note)}}} \\"
                     for k, v, note in summary)
    stamp = r.generated_at.strftime("%-d %B %Y, %H:%M UTC")

    parts = [rf"""\documentclass[10pt,a4paper]{{article}}
\usepackage[margin=18mm]{{geometry}}
\usepackage{{fontspec}}
\usepackage{{xcolor}}
\usepackage{{graphicx}}
\usepackage{{tikz}}
\usepackage{{booktabs}}
\usepackage{{array}}
\usepackage{{longtable}}
\setlength{{\LTleft}}{{0pt}}
\setlength{{\LTright}}{{0pt}}
\definecolor{{ink}}{{HTML}}{{0B0B0B}}
\definecolor{{inkmuted}}{{HTML}}{{52514E}}
\renewcommand{{\familydefault}}{{\sfdefault}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{4pt}}
\pagestyle{{plain}}
\begin{{document}}
{{\Large\bfseries Registration report\par}}
{{\large {esc(c.title)}\par}}
{{\color{{inkmuted}} {esc(c.date_range)} · generated {esc(stamp)} · no names or contact details\par}}
\vspace{{8pt}}
\begin{{tabular}}{{@{{}}l r l}}
{rows}
\end{{tabular}}

\vspace{{10pt}}
{{\bfseries How registrations split\par}}
{{\color{{inkmuted}}\footnotesize Each column accounts for every registration once. Boxes give the
exact count; each band's percentage is the share of the box it leaves.\par}}
\vspace{{6pt}}
\noindent\resizebox{{\linewidth}}{{!}}{{%
@@SANKEY@@}}
""".replace("@@SANKEY@@", tikz(sk))]

    if r.seats:
        parts.append(r"\vspace{8pt}{\bfseries Limited items\par}")
        parts.append(r"\begin{tabular}{l r r r}\toprule \textbf{Item} & \textbf{Taken} & "
                     r"\textbf{Max} & \textbf{Min} \\ \midrule")
        for s in r.seats.values():
            parts.append(rf"{esc(s.label)} & {s.taken} & {s.max if s.max is not None else '–'} & "
                         rf"{s.min if s.min is not None else '–'} \\")
        parts.append(r"\bottomrule\end{tabular}")

    parts.append(r"\clearpage{\Large\bfseries Breakdowns\par}"
                 r"{\color{inkmuted}\footnotesize Percentages are of all "
                 + str(r.total) + r" registrations. Where several answers could be chosen, "
                 r"rows can add up to more than the total.\par}")
    for b in [r.tiers, *r.sub_events, *r.fields]:
        note = r" {\color{inkmuted}\footnotesize (more than one answer allowed)}" if b.multi else ""
        # A minipage per table keeps a heading with its rows across pages;
        # the \par ends each one's paragraph so the space above takes effect.
        parts.append(r"\par\vspace{10pt}\noindent\begin{minipage}{\linewidth}"
                     rf"{{\bfseries {esc(b.label)}}}{note}\par\vspace{{2pt}}"
                     + _table_tex(b, r.statuses, r.total) + r"\end{minipage}\par")
    if r.other_answers:
        parts.append(_other_answers_tex(r.other_answers))
    parts.append(r"\end{document}")
    return "\n".join(parts)


def render_pdf(report: Report, sk: Sankey, *, currency_symbol: str = "$") -> bytes:
    """Compile the report through the shared tectonic queue. Raises
    documents.RenderError if the compile fails."""
    import shutil
    import tempfile
    from pathlib import Path

    from .documents import compile_prepared_dir

    job = Path(tempfile.mkdtemp(prefix="reg-report-"))
    try:
        tex_path = job / "report.tex"
        tex_path.write_text(latex(report, sk, currency_symbol=currency_symbol),
                            encoding="utf-8")
        return compile_prepared_dir(job, tex_path)
    finally:
        shutil.rmtree(job, ignore_errors=True)


def pdf_filename(conference, report: Report) -> str:
    return f"registrations-{conference.slug}-{report.generated_at:%Y%m%d-%H%M}.pdf"
