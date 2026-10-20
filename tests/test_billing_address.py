"""A registrant's billing address reaches every document billed to them.

The address is a form-builder field of type `billing-address`, so it lives in
the registration's custom_data under whatever key the admin chose. Documents
find it by type. Compiles are stubbed — the .tex handed to the compiler is
what is checked.
"""
from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.extensions import db
from app.services import documents as docs

ADDRESS = "Level 2, 100 Example Street\nAdelaide SA 5000"


def _schema(key="bill-to"):
    return {"sections": [{
        "key": "pay", "label": "Registration & Payment", "fields": [
            {"key": "tier-select", "type": "registration-tier",
             "label": "Tier", "required": True},
            {"key": "payment-method", "type": "radio", "label": "Payment method",
             "options": ["Credit Card", "Bank Transfer"], "required": True},
            {"key": key, "type": "billing-address", "label": "Billing address",
             "required": False},
        ]}]}


@pytest.fixture
def seed_templates(app):
    with app.app_context():
        from app.models import get_document_template
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
    return app


@pytest.fixture
def mailbox(monkeypatch):
    box: list[dict] = []
    monkeypatch.setattr("app.services.invoice.send_mail",
                        lambda **kw: box.append(kw) or True)
    monkeypatch.setattr("app.services.mail.send_mail",
                        lambda **kw: box.append(kw) or True)
    monkeypatch.setattr("app.services.payments.send_mail",
                        lambda **kw: box.append(kw) or True)
    return box


@pytest.fixture
def compiled(monkeypatch):
    """The .tex of every compile, in order; identical .tex, identical bytes."""
    import hashlib

    seen: list[str] = []

    def _record(tectonic, job_dir, tex_path, epoch, memory_mb=0,
                should_abort=None, timeout=0):
        tex = Path(tex_path).read_text(encoding="utf-8")
        seen.append(tex)
        return b"%PDF-" + hashlib.sha256(tex.encode()).hexdigest().encode()

    monkeypatch.setattr(docs, "_compile", _record)
    return seen


def _reg(app, *, schema, custom_data, status="pending", amount=11000):
    from app.models import Conference, Registration, User
    tag = secrets.token_hex(4)
    with app.app_context():
        u = User(email=f"bill-{tag}@example.org", full_name="Bill Payer",
                 role_name="member")
        db.session.add(u)
        db.session.flush()
        c = Conference(slug=f"bill-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
                       registration_form_schema=schema)
        db.session.add(c)
        db.session.flush()
        r = Registration(user_id=u.id, conference_id=c.id, tier_name="Standard",
                         amount=amount, status=status, custom_data=custom_data)
        db.session.add(r)
        db.session.commit()
        r.charge_to(amount, reason="Standard")
        return r.id


def test_lookup_is_by_type_not_key():
    from app.services.form_renderer import billing_address
    assert billing_address(_schema("anything"), {"anything": f"  {ADDRESS}\n"}) == ADDRESS
    assert billing_address(_schema(), {}) == ""
    assert billing_address(None, {"bill-to": ADDRESS}) == ""
    # A text field that happens to be called "billing-address" is not one.
    plain = {"sections": [{"fields": [{"key": "billing-address", "type": "text"}]}]}
    assert billing_address(plain, {"billing-address": ADDRESS}) == ""


@pytest.mark.parametrize("kind,status", [("invoice", "pending"),
                                         ("receipt", "paid"),
                                         ("adjustment", "refunded")])
def test_every_registration_document_prints_it(seed_templates, compiled,
                                               kind, status):
    from app.models import Registration
    from app.services.invoice import _registration_vars
    from app.services.documents import render_document
    app = seed_templates
    rid = _reg(app, schema=_schema(), custom_data={"bill-to": ADDRESS},
               status=status)
    with app.app_context():
        vars_ = _registration_vars(Registration.query.get(rid), kind)
        assert vars_["recipient_address"] == ADDRESS
        render_document(kind, vars_)

    tex = compiled[-1]
    assert "\\raddrtrue" in tex
    assert "Level 2, 100 Example Street \\\\\nAdelaide SA 5000" in tex


def test_no_field_means_no_address_line(seed_templates, compiled):
    from app.models import Registration
    from app.services.invoice import registration_document
    app = seed_templates
    rid = _reg(app, schema=None, custom_data=None)
    with app.app_context():
        registration_document(Registration.query.get(rid), "invoice")
    assert "\\raddrfalse" in compiled[-1]


def test_the_auto_receipt_snapshot_carries_it(seed_templates, compiled, mailbox):
    """The address is frozen into the issued document, so a later edit to the
    registration does not rewrite a receipt already sent."""
    from app.models import IssuedDocument, Registration
    from app.services.invoice import send_invoice_email
    app = seed_templates
    rid = _reg(app, schema=_schema(), custom_data={"bill-to": ADDRESS},
               status="paid")
    with app.app_context():
        reg = Registration.query.get(rid)
        send_invoice_email(reg)
        row = (IssuedDocument.query.filter_by(kind="receipt")
               .order_by(IssuedDocument.id.desc()).first())
        assert json.loads(row.vars_json)["recipient_address"] == ADDRESS
        before = docs.regenerate_document(row)

        reg.custom_data = {"bill-to": "Somewhere else entirely"}
        db.session.commit()
        assert docs.regenerate_document(row) == before


def test_manual_receipt_bills_whoever_the_invoice_billed(seed_templates, app,
                                                         mailbox, compiled):
    from app.services.invoice import _manual_receipt_vars, send_manual_invoice
    ref = f"INV-{secrets.token_hex(3)}"
    with app.app_context():
        send_manual_invoice("sponsor@example.org", recipient_name="Sponsor Pty Ltd",
                            description="Gold sponsorship", item="Gold",
                            amount_cents=50000, reference=ref,
                            recipient_abn="11 222 333 444",
                            recipient_address=ADDRESS)
        v = _manual_receipt_vars(ref, "sponsor@example.org", 50000)
    assert v["user_name"] == "Sponsor Pty Ltd"
    assert v["user_email"] == "sponsor@example.org"
    assert v["recipient_address"] == ADDRESS
    assert v["recipient_abn"] == "11 222 333 444"


def test_manual_receipt_without_an_invoice_snapshot_still_works(seed_templates,
                                                                app):
    from app.services.invoice import _manual_receipt_vars
    with app.app_context():
        v = _manual_receipt_vars("INV-NONE", "who@example.org", 100)
    assert v["user_name"] == "who@example.org"
    assert v["recipient_address"] == ""


def test_registration_form_saves_it(seeded, app, member_client, mailbox):
    from app.models import Conference, Registration
    from app.models.conference import PriceTier
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"form-{tag}", title="Physics 2026",
                       start_date=date.today() + timedelta(days=30),
                       end_date=date.today() + timedelta(days=32),
                       is_accepting_registrations=True, is_draft=False,
                       registration_form_schema=_schema())
        db.session.add(c)
        db.session.flush()
        db.session.add(PriceTier(conference_id=c.id, name="Free", amount=0,
                                 display_order=0))
        db.session.commit()
        slug = c.slug

    page = member_client.get(f"/conferences/{slug}/register")
    assert b'name="bill-to"' in page.data
    assert b"This will display in invoices and receipts sent." in page.data

    member_client.post(f"/conferences/{slug}/register", data={
        "tier": "Free", "payment-method": "Bank Transfer", "bill-to": ADDRESS,
    }, follow_redirects=True)
    with app.app_context():
        reg = (Registration.query.join(Conference)
               .filter(Conference.slug == slug).first())
        assert reg.custom_data["bill-to"] == ADDRESS


def test_form_builder_offers_and_keeps_the_type(seed_templates, admin_client, app):
    from app.models import Conference
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"fb-{tag}", title="Physics 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3),
                       registration_form_schema=_schema())
        db.session.add(c)
        db.session.commit()
        cid = c.id

    page = admin_client.get(f"/admin/conferences/{cid}/form-builder/registration")
    assert b'value="billing-address" selected' in page.data

    admin_client.post(f"/admin/conferences/{cid}/form-builder/registration", data={
        "action": "save_schema",
        "section_key[]": ["pay"], "section_label[]": ["Payment"],
        "field_key_0[]": ["bill-to"], "field_label_0[]": ["Billing address"],
        "field_type_0[]": ["billing-address"], "field_options_0[]": [""],
        "field_sub_event_0[]": [""], "field_cond_field_0[]": [""],
        "field_cond_value_0[]": [""],
    }, follow_redirects=True)
    with app.app_context():
        saved = db.session.get(Conference, cid).registration_form_schema
    assert saved["sections"][0]["fields"][0]["type"] == "billing-address"
