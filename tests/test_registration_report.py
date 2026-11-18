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
             "options": ["Vegan", "Halal", "Other"]},
            {"key": "diet-other", "type": "text", "label": "Other diet (please specify)",
             "condition": {"field": "diet", "contains": "Other"}},
            {"key": "waitlist", "type": "checkbox", "label": "Join the wait-list?",
             "condition": {"field": "dinner", "contains": "at-capacity"}},
            {"key": "note", "type": "text", "label": "Secret note"},
            {"key": "ws", "type": "sub-event", "label": "Workshop", "sub_event_id": se.id}]}]}
        db.session.commit()
        return {"id": c.id, "slug": c.slug, "se": se.id}


def _reg(app, conf, *, status="paid", tier="Student", dinner="Yes", diet=(),
         diet_other="", waitlist="", workshop=None, paid=0, deleted=False, name="Person"):
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
                                        "diet-other": diet_other, "waitlist": waitlist,
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
    _reg(app, conf, status="pending", dinner="at-capacity", waitlist="1")
    _reg(app, conf, status="paid", tier="Sponsor", dinner="")
    _reg(app, conf, status="refunded", paid=0)
    _reg(app, conf, status="paid", paid=0)                 # marked paid, nothing received
    _reg(app, conf, deleted=True, diet=["Other"], diet_other="Deleted person's diet")
    return conf


@pytest.fixture
def other_diets(app, conf):
    """Other dietary answers as people type them, plus the cases that must
    not be listed."""
    _reg(app, conf, diet=["Other"], diet_other="Nut allergy", name="Nutty Name")
    _reg(app, conf, diet=["Vegan", "Other"], diet_other="  nut   ALLERGY ")
    _reg(app, conf, status="pending", diet=["Other"], diet_other="Coeliac")
    _reg(app, conf, diet=["Other"], diet_other="")                  # chose Other, typed nothing
    _reg(app, conf, diet=["Vegan"], diet_other="Left over from unticking Other")
    _reg(app, conf, deleted=True, diet=["Other"], diet_other="Deleted")
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
    # The same people counted once each, by exactly what they ticked: both
    # and neither show, and the rows add up to everyone.
    assert [(a, sum(c.values())) for a, c in diet.combos] == [
        ("Vegan only", 1), ("Vegan + Halal", 1), (rr.NONE_TICKED, 4)]
    assert sum(sum(c.values()) for _, c in diet.combos) == r.total
    assert fields["f:dinner"].combos is None            # single-answer: no second table
    tiers = dict((a, dict(c)) for a, c in r.tiers.rows)
    assert tiers["Student"] == {"paid": 3, "pending": 1, "refunded": 1}
    ws = dict((a, sum(c.values())) for a, c in r.sub_events[0].rows)
    assert ws == {"Attending": 1, "Not attending": 1, "(not asked)": 4}
    assert r.seats["dinner"].taken == 2                 # refunded gave theirs back
    # Shown only once the dinner is full: everyone else was never asked,
    # which is not the same as answering No.
    waitlist = dict((a, sum(c.values())) for a, c in fields["f:waitlist"].rows)
    assert waitlist == {"Yes": 1, rr.NOT_ASKED: 5}


def test_other_answers_are_listed_grouped_and_counted(app, other_diets):
    from app.services import registration_report as rr
    with app.app_context():
        r = rr.build(_conference(app, other_diets))
    assert len(r.other_answers) == 1                  # only the "Other" follow-up
    g = r.other_answers[0]
    assert (g.question, g.detail) == ("Diet", "Other diet (please specify)")
    # Grouped regardless of case and spacing, first spelling kept, most first.
    assert g.answers == [("Nut allergy", 2), ("Coeliac", 1)]
    assert g.blank == 1 and g.total == 4
    # Matches the breakdown's Other row: everyone who chose it, nobody else.
    diet = next(b for b in r.fields if b.key == "f:diet")
    assert sum(dict(diet.rows)["Other"].values()) == g.total


def test_the_appendix_in_the_pdf_and_on_the_page(seeded, admin_client, app, other_diets):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, other_diets)
        tex = rr.latex(rr.build(c), rr.build_sankey(c, ["status"]))
    assert r"Appendix: ``Other'' answers" in tex
    assert "Nut allergy & 2" in tex and "Coeliac & 1" in tex and "(left blank)} & 1" in tex
    for absent in ("Left over from unticking", "Deleted", "Nutty", "note from"):
        assert absent not in tex
    page = admin_client.get(f"/admin/conferences/{other_diets['id']}/report").data.decode()
    assert "“Other” answers" in page and "Nut allergy" in page and "Nutty" not in page


def test_no_appendix_without_other_answers(app, seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        r = rr.build(c)
        assert r.other_answers == []
        assert "Appendix" not in rr.latex(r, rr.build_sankey(c, ["status"]))


def _pay(app, reg_id, event_type, amount, txn=""):
    from app.models import record_payment_event
    with app.app_context():
        record_payment_event(registration_id=reg_id, transaction_id=txn,
                             merchant_reference=f"reg_{reg_id}",
                             event_type=event_type, amount=amount)


def test_how_registrations_were_paid(app, conf):
    """Each registration's settlement comes from the ledger, not its status."""
    from app.services import registration_report as rr
    gw = _reg(app, conf, paid=40000)                       # Worldline capture
    _pay(app, gw, "payment.captured", 40000, txn=f"T{gw}")  # webhook redelivered
    man = _reg(app, conf)                                   # bank transfer, marked by hand
    _pay(app, man, "manual.paid", 40000)
    mix = _reg(app, conf, paid=10000)
    _pay(app, mix, "manual.paid", 30000)
    rec = _reg(app, conf)                                   # found by reconciliation
    _pay(app, rec, "reconcile.captured", 40000, txn="R1")
    # Paid in full, then billed for a workshop: still marked paid, owes $50.
    later = _reg(app, conf, paid=40000)
    with app.app_context():
        reg = db.session.get(Registration, later)
        reg.charge_to(45000, reason="Workshop billed")
    _reg(app, conf, status="pending", paid=10000)          # part paid
    _reg(app, conf, status="pending")                      # nothing yet
    _reg(app, conf, tier="Sponsor")                        # no fee
    _reg(app, conf)                                        # marked paid, nothing anywhere
    refunded = _reg(app, conf, status="refunded", paid=40000)
    _pay(app, refunded, "payment.refunded", 40000, txn=f"T{refunded}")

    with app.app_context():
        r = rr.build(_conference(app, conf))
    rows = {key: (n, received, out) for key, _, n, received, out in r.settlements}
    assert rows == {
        "worldline": (2, 80000, 0),          # card + reconciliation; redelivery counted once
        "manual": (1, 40000, 0),
        "mixed": (1, 40000, 0),
        "part": (2, 50000, 35000),           # 400 + 100 received; 50 + 300 owed
        # Pending with nothing, and marked paid with nothing received: the
        # status says paid, the ledger says the fee is still owed.
        "unpaid": (2, 0, 80000),
        "free": (1, 0, 0),
        "refunded": (1, 0, 0),               # captured then refunded: nets to nothing
    }
    assert [k for k, *_ in r.settlements] == [
        "worldline", "manual", "mixed", "part", "unpaid", "free", "refunded"]
    assert sum(n for n, _, _ in rows.values()) == r.total
    assert sum(rec for _, rec, _ in rows.values()) == r.received
    assert sum(out for _, _, out in rows.values()) == r.outstanding
    assert r.received_by_source == {"worldline": 140000, "manual": 70000}
    assert r.received_note() == ("net of refunds: $1,400.00 via Worldline, "
                                 "$700.00 recorded manually")
    with app.app_context():
        c = _conference(app, conf)
        sk = rr.build_sankey(c, ["settlement", "status"])
        tex = rr.latex(r, sk)
    labels = {n.label: n.count for n in sk.columns[1]}
    assert labels["Paid via Worldline"] == 2 and labels["Part paid"] == 2
    part_to_paid = next(lk for lk in sk.links
                        if lk.source.label == "Part paid" and lk.target.label == "Paid")
    assert part_to_paid.count == 1                          # the workshop one
    assert r"\textbf{How paid}" in tex and "Paid, recorded manually & 1 & \\$400.00" in tex


def test_combinations_follow_the_options_not_the_order_ticked(app):
    from app.services import registration_report as rr
    f = {"key": "m", "type": "checkbox-group", "options": ["DDA Member", "ASMI Member"]}
    assert rr._combination(f, {"m": ["ASMI Member", "DDA Member"]}) == "DDA Member + ASMI Member"
    assert rr._combination(f, {"m": ["ASMI Member"]}) == "ASMI Member only"
    assert rr._combination(f, {"m": []}) == rr.NONE_TICKED
    assert rr._combination(f, {}) == rr.NONE_TICKED
    assert rr._all_combinations(f) == ["DDA Member only", "ASMI Member only",
                                       "DDA Member + ASMI Member"]


def test_the_combination_table_on_the_page_and_in_the_pdf(seeded, admin_client, app,
                                                          seeded_regs):
    from app.services import registration_report as rr
    with app.app_context():
        c = _conference(app, seeded_regs)
        tex = rr.latex(rr.build(c), rr.build_sankey(c, ["status"]))
    assert r"\textbf{Ticked}" in tex and "Vegan + Halal & 1" in tex
    assert r"\textit{Total}" in tex
    page = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/report").data.decode()
    assert 'class="table report-table combos"' in page and "Vegan + Halal" in page


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
        assert "f:note" not in keys                             # free text never
        assert "f:diet" in keys                                 # by combination
        sk = rr.build_sankey(c, ["f:diet"])
        assert {n.label: n.count for n in sk.columns[1]} == {
            "Vegan only": 1, "Vegan + Halal": 1, rr.NONE_TICKED: 4}
        assert rr.default_stages(c) == ["settlement", "tier", "f:dinner"]   # the limited one
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


# --- the PDF as a background job, with progress -----------------------------

@pytest.fixture
def no_tectonic_needed(monkeypatch):
    monkeypatch.setattr("app.services.documents._resolve_tectonic", lambda: "tectonic")


def _start(client, conf, *stages):
    r = client.post(f"/admin/conferences/{conf['id']}/report/jobs",
                    data={"stage": list(stages)})
    assert r.status_code == 202, r.data
    return r.get_json()


def test_a_job_reports_its_steps_and_serves_the_pdf(seeded, admin_client, app, seeded_regs,
                                                    no_tectonic_needed):
    j = _start(admin_client, seeded_regs, "status", "tier")
    assert j["started"] is True
    s = admin_client.get(j["status_url"])
    assert s.headers["Cache-Control"] == "no-store"
    s = s.get_json()
    assert s["state"] == "done" and s["error"] is None
    assert [(st["key"], st["state"]) for st in s["steps"]] == [
        ("count", "done"), ("chart", "done"), ("typeset", "done"), ("ready", "done")]
    assert all(set(st) == {"key", "label", "state"} for st in s["steps"])
    assert "user_id" not in s and "conference_id" not in s
    pdf = admin_client.get(j["download_url"])
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert f"registrations-{seeded_regs['slug']}-" in pdf.headers["Content-Disposition"]


def test_a_job_is_only_its_owners(seeded, admin_client, app, seeded_regs,
                                  login_user_session, no_tectonic_needed):
    from flask import g

    j = _start(admin_client, seeded_regs, "status")
    # Another admin, with the same permission, cannot see or fetch it.
    login_user_session(email="admin2@test.example.org", role_name="admin")
    # pytest-flask keeps one app context open for the whole test, and
    # Flask-Login caches the signed-in user on its `g`; without this, every
    # request would still be answered as the first admin.
    g.pop("_login_user", None)
    assert admin_client.get(j["status_url"]).status_code == 404
    assert admin_client.get(j["download_url"]).status_code == 404
    # Nor can anything that is not a job id we minted.
    base = f"/admin/conferences/{seeded_regs['id']}/report/jobs"
    assert admin_client.get(f"{base}/..%2F..%2Fapp.db").status_code == 404
    assert admin_client.get(f"{base}/nope").status_code == 404


def test_members_cannot_start_a_job(seeded, member_client, seeded_regs):
    r = member_client.post(f"/admin/conferences/{seeded_regs['id']}/report/jobs")
    assert r.status_code in (302, 403)


def test_pressing_again_while_running_returns_the_same_job(app, seeded_regs, monkeypatch):
    """A second press — another tab, a double click — finds the running job."""
    from app.models import Conference
    from app.services import report_jobs

    ran = []
    monkeypatch.setattr(report_jobs, "_run", lambda *a: ran.append(a))
    with app.test_request_context():
        c = db.session.get(Conference, seeded_regs["id"])
        first, started = report_jobs.start(c, ["status"], user_id=42)
        again, started_again = report_jobs.start(c, ["status"], user_id=42)
        other_cols, _ = report_jobs.start(c, ["tier"], user_id=42)
        other_user, _ = report_jobs.start(c, ["status"], user_id=43)
    assert started and not started_again and again == first
    assert len({first, other_cols, other_user}) == 3
    assert len(ran) == 3                                # the repeat started nothing


def test_a_failed_compile_is_reported_not_downloaded(seeded, admin_client, app, seeded_regs,
                                                     monkeypatch):
    from app.services.documents import RenderError

    def fail(*a, **k):
        raise RenderError("tectonic not found")
    monkeypatch.setattr("app.services.registration_report.render_pdf", fail)
    j = _start(admin_client, seeded_regs, "status")
    s = admin_client.get(j["status_url"]).get_json()
    assert s["state"] == "error" and s["error"] == "tectonic not found"
    assert admin_client.get(j["download_url"]).status_code == 404


def test_a_lost_job_says_so_and_old_ones_are_swept(app, seeded_regs, monkeypatch):
    import json
    import os
    import time

    from app.models import Conference
    from app.services import report_jobs

    monkeypatch.setattr(report_jobs, "_run", lambda *a: None)
    with app.test_request_context():
        c = db.session.get(Conference, seeded_regs["id"])
        lost, _ = report_jobs.start(c, ["status"], user_id=7)
        d = report_jobs._root() / lost
        status = json.loads((d / "status.json").read_text())
        status["updated"] = time.time() - 3600         # the worker died mid-job
        (d / "status.json").write_text(json.dumps(status))
        s = report_jobs.read(lost)
        assert s["state"] == "error" and "restarted" in s["error"]
        # A lost job is not "running", so pressing again starts afresh.
        fresh, started = report_jobs.start(c, ["status"], user_id=7)
        assert started and fresh != lost
        old = time.time() - 7200
        os.utime(d, (old, old))
        report_jobs.start(c, ["tier"], user_id=7)       # any start sweeps
        assert not d.exists()


def test_the_wait_for_the_typesetter_is_shown_only_when_there_was_one():
    import time

    from app.services.report_jobs import public
    now = time.time()
    base = {"state": "running", "started": now - 5, "done": ["count", "chart"]}
    shown = lambda s: [(st["key"], st["state"]) for st in public(s)["steps"]]  # noqa: E731

    # Just handed over: typesetting reads as under way, no "waiting" line.
    assert shown({**base, "step": "queued", "queued_at": now - 0.2}) == [
        ("count", "done"), ("chart", "done"), ("typeset", "current"), ("ready", "todo")]
    # Stuck behind another compile: the wait is the step under way.
    assert shown({**base, "step": "queued", "queued_at": now - 4}) == [
        ("count", "done"), ("chart", "done"), ("queued", "current"),
        ("typeset", "todo"), ("ready", "todo")]
    # Once typesetting, a real wait stays listed as done; a blink does not.
    typesetting = {**base, "step": "typeset", "done": ["count", "chart", "queued"]}
    assert ("queued", "done") in shown({**typesetting, "waited": 4})
    assert "queued" not in dict(shown({**typesetting, "waited": 0.1}))


def test_a_compile_says_when_it_really_starts():
    """documents calls on_start once the box-wide lock is held, before
    tectonic runs, and only once; the abort check still works."""
    from pathlib import Path

    from app.services.documents import _CompileJob

    calls = []
    job = _CompileJob("tectonic", Path("."), Path("x.tex"), 0,
                      on_start=lambda: calls.append("start"))
    assert job._starting() is False and job._starting() is False
    assert calls == ["start"]
    job.abandoned = True
    assert job._starting() is True
    # A failing callback never stops the compile.
    bad = _CompileJob("tectonic", Path("."), Path("x.tex"), 0, on_start=lambda: 1 / 0)
    assert bad._starting() is False


def test_the_page_offers_progress(seeded, admin_client, seeded_regs):
    page = admin_client.get(f"/admin/conferences/{seeded_regs['id']}/report").data.decode()
    assert 'id="pdf-progress"' in page and "data-start-url=" in page
    # The plain download stays as the fallback without script.
    assert f"/admin/conferences/{seeded_regs['id']}/report.pdf" in page


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
