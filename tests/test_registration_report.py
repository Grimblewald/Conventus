"""The conference registration report: counts, breakdowns, Sankey, PDF.

Counted from the registrations at the moment it is asked for, deleted ones
left out. Only aggregate counts leave the report — no names, emails or
free-text answers — so it can be passed around a committee.
"""
from __future__ import annotations

import secrets
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Registration


@pytest.fixture
def conf(app):
    """A conference with two tiers, a dinner question, a dietary
    checkbox-group, a free-text note, and a workshop."""
    from app.models import Conference, SubEvent
    from app.models.conference import PriceTier

    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"rep-{tag}", title="Report 2026",
                       start_date=date.today() + timedelta(days=30),
                       end_date=date.today() + timedelta(days=32),
                       is_accepting_registrations=True, is_draft=False)
        db.session.add(c)
        db.session.flush()
        db.session.add_all([
            PriceTier(conference_id=c.id, name="Student", amount=40000, display_order=0),
            PriceTier(conference_id=c.id, name="Sponsor", amount=0, display_order=1)])
        se = SubEvent(conference_id=c.id, name="Workshop", price=5000)
        db.session.add(se)
        db.session.flush()
        c.registration_form_schema = {"sections": [{"key": "p", "label": "P", "fields": [
            {"key": "tier-select", "type": "registration-tier", "label": "Tier"},
            {"key": "dinner", "type": "radio", "label": "Dinner",
             "options": ["Yes", "No"], "max_attendees": 10, "counts_option": "Yes",
             "full_value": "at-capacity"},
            {"key": "diet", "type": "checkbox-group", "label": "Diet",
             "options": ["Vegan", "Halal"]},
            {"key": "note", "type": "text", "label": "Secret note"},
            {"key": "ws", "type": "sub-event", "label": "Workshop", "sub_event_id": se.id}]}]}
        db.session.commit()
        return {"id": c.id, "slug": c.slug, "se": se.id}


def _reg(app, conf, *, status="paid", tier="Student", dinner="Yes", diet=(),
         workshop=None, paid=0, deleted=False, name="Person"):
    from app.models import User, record_payment_event
    with app.app_context():
        u = User(email=f"{secrets.token_hex(4)}@private.example.org",
                 full_name=name, role_name="member")
        db.session.add(u)
        db.session.flush()
        amount = 40000 if tier == "Student" else 0
        reg = Registration(
            user_id=u.id, conference_id=conf["id"], tier_name=tier, amount=amount,
            status=status, custom_data={"dinner": dinner, "diet": list(diet),
                                        "note": f"note from {name}"},
            sub_events=({"workshop": {"attending": workshop}}
                        if workshop is not None else None),
            deleted_at=datetime.utcnow() if deleted else None)
        db.session.add(reg)
        db.session.commit()
        reg.charge_to(amount, reason=tier)
        if paid:
            record_payment_event(registration_id=reg.id, transaction_id=f"T{reg.id}",
                                 merchant_reference=f"reg_{reg.id}",
                                 event_type="payment.captured", amount=paid)
        return reg.id


@pytest.fixture
def seeded_regs(app, conf):
    _reg(app, conf, paid=40000, diet=["Vegan", "Halal"], workshop=True, name="Alice Secret")
    _reg(app, conf, paid=40000, dinner="No", diet=["Vegan"], workshop=False)
    _reg(app, conf, status="pending", dinner="at-capacity")
    _reg(app, conf, status="paid", tier="Sponsor", dinner="")
    _reg(app, conf, status="refunded", paid=0)
    _reg(app, conf, status="paid", paid=0)                 # marked paid, nothing received
    _reg(app, conf, deleted=True)
    return conf


def _conference(app, conf):
    from app.models import Conference
    return db.session.get(Conference, conf["id"])


def test_counts(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        r = rr.build(_conference(app, seeded_regs))
    assert r.total == 6 and r.deleted == 1
    assert dict(r.by_status) == {"paid": 4, "pending": 1, "refunded": 1}
    assert r.statuses == ["paid", "pending", "refunded"]
    assert (r.paid_with_fee, r.paid_no_fee, r.paid_unrecorded) == (3, 1, 1)
    assert r.received == 80000
    # Pending owes the full fee; the paid one with nothing received owes too.
    assert r.outstanding == 80000
    assert r.owing_note == "owed by 2 registrations (1 paid with a balance, 1 pending)"
    assert r.active == 5
    assert "1 marked paid with no payment recorded" in r.paid_note


def test_breakdowns(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        r = rr.build(_conference(app, seeded_regs))
    fields = {b.key: b for b in r.fields}
    assert "f:note" not in fields                       # free text never counted
    dinner = dict((a, sum(c.values())) for a, c in fields["f:dinner"].rows)
    assert dinner == {"Yes": 3, "No": 1, "at-capacity": 1, rr.NO_ANSWER: 1}
    assert [a for a, _ in fields["f:dinner"].rows][-1] == rr.NO_ANSWER
    diet = fields["f:diet"]
    assert diet.multi
    assert dict((a, sum(c.values())) for a, c in diet.rows) == {
        "Vegan": 2, "Halal": 1, rr.NO_ANSWER: 4}
    tiers = dict((a, dict(c)) for a, c in r.tiers.rows)
    assert tiers["Student"] == {"paid": 3, "pending": 1, "refunded": 1}
    ws = dict((a, sum(c.values())) for a, c in r.sub_events[0].rows)
    assert ws == {"Attending": 1, "Not attending": 1, "(not asked)": 4}
    assert r.seats["dinner"].taken == 2                 # refunded gave theirs back


def test_long_breakdowns_fold_into_other(app):
    from collections import Counter

    from app.services.registration_report import _fold
    rows = [(f"Country {i}", Counter({"paid": 20 - i})) for i in range(20)]
    folded = _fold(rows, 15)
    assert len(folded) == 15 and folded[-1][0] == "Other"
    assert sum(sum(c.values()) for _, c in folded) == sum(sum(c.values()) for _, c in rows)


def test_sankey_columns_and_bands_add_up(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        sk = rr.build_sankey(c, ["status", "tier", "f:dinner"])
    assert [s[0] for s in sk.stages] == ["all", "status", "tier", "f:dinner"]
    for col in sk.columns:
        assert sum(n.count for n in col) == 6
    for col in sk.columns[:-1]:
        for n in col:
            out = [lk for lk in sk.links if lk.source is n]
            assert sum(lk.count for lk in out) == n.count
            assert abs(sum(lk.share for lk in out) - 1) < 1e-9
    paid = next(n for n in sk.columns[1] if n.label == "Paid")
    to_student = next(lk for lk in sk.links if lk.source is paid and lk.target.label == "Student")
    assert to_student.count == 3 and rr.percent(to_student.share) == "75%"
    # Bands stack inside their nodes without gaps or overlap.
    for n in (n for col in sk.columns for n in col):
        out = sorted((lk for lk in sk.links if lk.source is n), key=lambda lk: lk.sy)
        if out:
            assert abs(out[0].sy - n.y) < 1e-6
            assert abs(out[-1].sy + out[-1].h - (n.y + n.h)) < 1e-6


def test_stage_choices(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        keys = [k for k, _ in rr.stage_options(c)]
        assert "f:diet" not in keys and "f:note" not in keys   # not one answer each
        assert rr.default_stages(c) == ["status", "tier", "f:dinner"]   # the limited one
        assert rr.parse_stages(c, ["tier", "bogus", "tier", "status"]) == ["tier", "status"]
        assert rr.parse_stages(c, ["bogus"]) == rr.default_stages(c)


def test_percent_never_rounds_a_band_away():
    from app.services.registration_report import percent
    assert percent(0.001) == "<1%" and percent(0.999) == ">99%"
    assert percent(1) == "100%" and percent(0) == "0%" and percent(0.256) == "26%"


def test_nothing_personal_leaves_the_report(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        r = rr.build(c)
        sk = rr.build_sankey(c, ["status", "tier", "f:dinner"])
        out = rr.svg(sk) + rr.latex(r, sk)
    assert "Alice" not in out and "private.example.org" not in out
    assert "note from" not in out


def test_the_page(seeded, admin_client, app, seeded_regs):
    page = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/report"
                            "?stage=status&stage=f:dinner")
    assert page.status_code == 200
    body = page.data.decode()
    assert "How registrations split" in body and "<svg class=\"sankey\"" in body
    # Paid: Alice (Yes), No, the sponsor (no answer), the unrecorded one (Yes).
    assert 'data-tip="Paid → Yes: 2 (50% of Paid)"' in body
    assert "Alice" not in body
    report_url = f"/admin/conferences/{seeded_regs['id']}/report"
    edit = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/edit").data.decode()
    assert report_url in edit
    assert report_url in admin_client.get("/admin/conferences").data.decode()


def test_members_cannot_see_it(seeded, member_client, seeded_regs):
    r = member_client.get(f"/admin/conferences/{seeded_regs['id']}/report")
    assert r.status_code in (302, 403)
    r = member_client.get(f"/admin/conferences/{seeded_regs['id']}/report.pdf")
    assert r.status_code in (302, 403)


def test_the_pdf_download(seeded, admin_client, app, seeded_regs, monkeypatch):
    # The compile itself is stubbed by conftest; the binary need not exist.
    monkeypatch.setattr("app.services.documents._resolve_tectonic", lambda: "tectonic")
    r = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/report.pdf"
                         "?stage=status&stage=tier")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF")
    assert "attachment" in r.headers["Content-Disposition"]
    assert f"registrations-{seeded_regs['slug']}-" in r.headers["Content-Disposition"]


def test_a_failed_compile_goes_back_to_the_page(seeded, admin_client, app,
                                                seeded_regs, monkeypatch):
    from app.services.documents import RenderError

    def fail(*a, **k):
        raise RenderError("tectonic not found")
    monkeypatch.setattr("app.services.registration_report.render_pdf", fail)
    r = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/report.pdf")
    assert r.status_code == 302
    assert f"/admin/conferences/{seeded_regs['id']}/report" in r.headers["Location"]


def _tectonic_available() -> bool:
    return bool(shutil.which("tectonic") or (Path.home() / ".local/bin/tectonic").exists())


@pytest.mark.real_latex
@pytest.mark.skipif(not _tectonic_available(), reason="tectonic is not installed")
def test_the_latex_compiles(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        r = rr.build(c)
        sk = rr.build_sankey(c, ["status", "tier", "f:dinner"], width=480, height=330)
        pdf = rr.render_pdf(r, sk)
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000
