"""Tests for the one document renderer (app/services/documents.py).

tectonic is a hard dependency, so these compile for real — there is no
skip-if-missing path. Kept to ~8 compiles total (each ~1-3s warm).
"""
from __future__ import annotations

import os
import shutil
import types

import pytest

from app.services.documents import (
    RawLatex, RenderError, assemble_tex, latex_escape, render_document,
    _doc_render_root,
)
from app.services import documents as docs


def _write_png(path):
    """A tiny but valid PNG to exercise the logo/asset path."""
    from PIL import Image
    Image.new("RGB", (8, 8), (40, 40, 40)).save(path)


@pytest.fixture(autouse=True)
def _clean_render_root():
    """Empty the (ours-to-manage) render root before each test so the
    orphan-cleanup assertions reflect only this test's renders."""
    root = _doc_render_root()
    if root.exists():
        for p in root.iterdir():
            shutil.rmtree(p, ignore_errors=True)
    yield


def _vars(**over):
    v = {
        "user_name": "Ada Lovelace",
        "user_email": "ada@example.org",
        "conference_title": "Symposium on Physics 2026",
        "conference_dates": "1-3 September 2026",
        "tier_name": "Standard",
        "amount": "110.00",
        "gst_amount": "10.00",
        "amount_ex_gst": "100.00",
        "currency_code": "AUD",
        "currency_symbol": "$",
        "transaction_id": "INV-000123",
        "payment_date": "21 July 2026",
        "due_date": "4 August 2026",
        "site_name": "Test Society",
        "registration_id": "42",
        "invoice_type": "Tax Invoice",
        "business_number": "11 222 333 444",
        "recipient_abn": "98 765 432 109",
        "payment_instructions": "EFT to BSB 000-000 Acct 12345678.",
    }
    v.update(over)
    return v


@pytest.fixture
def ctx(app):
    """App context with the three document templates seeded and GST on for the
    invoice so the GST breakdown rows are exercised."""
    from app.extensions import db
    from app.models import get_document_template
    with app.app_context():
        for kind in ("invoice", "receipt", "adjustment"):
            get_document_template(kind)
        inv = get_document_template("invoice")
        inv.gst_registered = True
        inv.business_number = "11 222 333 444"
        inv.pdf_body = "Thank you for registering for {conference_title}."
        db.session.commit()
        yield app


# --- (a) happy path + cleanup ------------------------------------------------

def test_invoice_renders_and_cleans(ctx, tmp_path):
    logo = tmp_path / "logo.png"
    _write_png(logo)
    pdf = render_document("invoice", _vars(), assets={"logo": logo})
    assert pdf[:4] == b"%PDF"
    # Job dir removed in finally; the render root is left empty (no pregen yet).
    root = _doc_render_root()
    assert not any(root.iterdir())


# --- (b) determinism ---------------------------------------------------------

def test_determinism(ctx):
    a = render_document("invoice", _vars())
    b = render_document("invoice", _vars())
    assert a == b
    assert a[:4] == b"%PDF"


# --- (c) escaping: LaTeX metacharacters compile and stay literal -------------

def test_escaping_compiles_and_neutralises_injection(ctx):
    nasty = r"& % _ $ # { } \ ~ ^ \input{/etc/passwd}"
    pdf = render_document("invoice", _vars(user_name=nasty,
                                           payment_instructions=nasty))
    assert pdf[:4] == b"%PDF"


def test_latex_escape_transformation():
    # Character table (matches the abstract-booklet escaping it replaced).
    assert latex_escape("&") == r"\&"
    assert latex_escape("%") == r"\%"
    assert latex_escape("$") == r"\$"
    assert latex_escape("#") == r"\#"
    assert latex_escape("_") == r"\_"
    assert latex_escape("{") == r"\{"
    assert latex_escape("}") == r"\}"
    assert latex_escape("~") == r"\textasciitilde{}"
    assert latex_escape("^") == r"\^{}"
    # Backslash goes through a sentinel so \textbackslash{}'s own braces
    # survive the brace escaping (same trick as the booklet body escaping).
    assert latex_escape("\\") == r"\textbackslash{}"
    # An injection attempt becomes inert literal text — no live control seq.
    out = latex_escape(r"\input{/etc/passwd}")
    assert r"\input" not in out
    assert out == r"\textbackslash{}input\{/etc/passwd\}"
    # A plain single-line string is unchanged (booklet compatibility).
    assert latex_escape("Plain citation, 2026.") == "Plain citation, 2026."


# --- (d) RawLatex bypasses escaping (assembly-level, no compile) -------------

def test_rawlatex_bypasses_escaping(app):
    tpl = types.SimpleNamespace(pdf_body="", gst_registered=False)
    with app.app_context():
        tex_escaped = assemble_tex("invoice", tpl, _vars(user_name=r"a_b&c"))
        tex_raw = assemble_tex("invoice", tpl,
                               _vars(user_name=RawLatex(r"\textbf{live}")))
    # Escaped value: metacharacters are neutralised in the source.
    assert r"a\_b\&c" in tex_escaped
    # RawLatex value: injected verbatim.
    assert r"\textbf{live}" in tex_raw


def test_rawlatex_pdf_body_bypasses(app):
    tpl = types.SimpleNamespace(pdf_body=RawLatex(r"\textit{raw body}"),
                                gst_registered=False)
    with app.app_context():
        tex = assemble_tex("invoice", tpl, _vars())
    assert r"\textit{raw body}" in tex


# --- (e) missing tectonic binary --------------------------------------------

def test_missing_tectonic_raises(ctx, monkeypatch):
    monkeypatch.setitem(ctx.config, "TECTONIC_BIN", "/nonexistent/tectonic-xyz")
    with pytest.raises(RenderError) as ei:
        render_document("invoice", _vars())
    assert "not found" in str(ei.value)


# --- (f) broken body → RenderError with a non-empty log ---------------------

def test_broken_body_raises_with_log(ctx, monkeypatch):
    broken = types.SimpleNamespace(
        pdf_body=RawLatex(r"\begin{tabular}{cc} never closed"),
        gst_registered=False)
    # Force the renderer to compile the broken (raw) body.
    monkeypatch.setattr("app.models.get_document_template",
                        lambda kind: broken)
    with pytest.raises(RenderError) as ei:
        render_document("invoice", _vars())
    assert ei.value.log.strip()
    # And the job dir was still cleaned up on the failure path.
    assert not any(_doc_render_root().iterdir())


# --- (g) all three kinds render ---------------------------------------------

def test_all_three_kinds_render(ctx):
    for kind in ("invoice", "receipt", "adjustment"):
        pdf = render_document(kind, _vars())
        assert pdf[:4] == b"%PDF", kind


# --- preview: bold-placeholder fill (assembly-level, no compile) -------------

def test_placeholder_fill_bold_names(ctx):
    """Every unset variable — including the numeric ones — renders as its bold
    field name, never a computed $0.00."""
    from app.services.documents import assemble_tex, placeholder_vars
    from app.models import get_document_template
    tpl = get_document_template("invoice")
    tex = assemble_tex("invoice", tpl, placeholder_vars("invoice"))
    assert r"\textbf{amount}" in tex
    assert r"\textbf{gst\_amount}" in tex
    assert r"\textbf{amount\_ex\_gst}" in tex
    assert "$0.00" not in tex


def test_pdf_body_rawlatex_var_renders_raw_with_escaped_literals(app):
    """The pdf_body fix: a RawLatex value substituted into {var} stays live
    LaTeX while the literal text around it is still escaped."""
    tpl = types.SimpleNamespace(pdf_body="Total {amount} due now & later",
                                gst_registered=False)
    with app.app_context():
        tex = assemble_tex("invoice", tpl,
                           _vars(amount=RawLatex(r"\textbf{amount}")))
    assert r"\textbf{amount}" in tex        # RawLatex var injected raw
    assert r"due now \& later" in tex       # surrounding literal still escaped


# --- preview: warm pregen + serve-vs-recompile rule --------------------------

def test_warm_pregen_and_serve_skips_tectonic(ctx, monkeypatch):
    """A first warm compiles for real and caches; serving a matching (saved)
    template then returns the cached bytes without invoking the renderer."""
    dest = docs.warm_pregen("invoice")      # real compile (1)
    assert dest.exists()
    assert dest.read_bytes()[:4] == b"%PDF"

    calls = []
    real = docs.render_document
    monkeypatch.setattr(docs, "render_document",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    pdf = docs.preview_pdf("invoice")       # no overrides, saved hash → pregen
    assert pdf[:4] == b"%PDF"
    assert calls == []                       # served from cache, no recompile


def test_override_recompiles_for_real(ctx):
    """Any data override takes the recompile branch and compiles a fresh PDF."""
    pdf = docs.preview_pdf("invoice", overrides={"user_name": "Real Person"})
    assert pdf[:4] == b"%PDF"


def test_serve_vs_recompile_decision(ctx, monkeypatch):
    """The exact §5 rule, counted with a stubbed renderer: match+no-override
    serves the cache (compiling once to populate it); an override or a
    hash-mismatched draft recompiles."""
    from app.models import DocumentTemplate
    calls = []
    monkeypatch.setattr(docs, "render_document",
                        lambda *a, **k: (calls.append(1), b"%PDF-fake")[1])

    docs.preview_pdf("invoice")             # cache miss → warm → 1 compile
    assert len(calls) == 1
    docs.preview_pdf("invoice")             # cache hit → no recompile
    assert len(calls) == 1
    docs.preview_pdf("invoice", overrides={"amount": "5.00"})   # override
    assert len(calls) == 2
    draft = DocumentTemplate(kind="invoice", pdf_body="a different body")
    docs.preview_pdf("invoice", template=draft)                 # hash mismatch
    assert len(calls) == 3


def test_pregen_busy_when_warm_in_progress(ctx):
    """A preview that needs the pregen while a warm holds the per-kind lock gets
    PregenBusy rather than starting a second compile."""
    lock = docs._pregen_lock("invoice")
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(docs.PregenBusy):
            docs.get_pregen("invoice")       # file absent (root cleaned) + locked
    finally:
        lock.release()


def test_preview_writes_no_payment_event(ctx, monkeypatch):
    """A preview is a pure caller of the renderer — it never records a ledger
    row."""
    from app.models import PaymentEvent
    monkeypatch.setattr(docs, "render_document", lambda *a, **k: b"%PDF-fake")
    before = PaymentEvent.query.count()
    pdf = docs.preview_document("invoice", overrides={"amount": "9.99"})
    assert pdf == b"%PDF-fake"
    assert PaymentEvent.query.count() == before


# --- preview: route + editor button ------------------------------------------

def test_document_preview_route_returns_pdf(ctx, admin_client, monkeypatch):
    """The POST route streams a PDF download and persists nothing."""
    from app.models import PaymentEvent
    monkeypatch.setattr(docs, "render_document", lambda *a, **k: b"%PDF-1.4 fake")
    before = PaymentEvent.query.count()
    resp = admin_client.post("/admin/financial/document/preview", data={
        "kind": "invoice", "pdf_body": "Body {amount}",
        "business_number": "X", "gst_registered": "1",
    })
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    disp = resp.headers.get("Content-Disposition", "")
    assert "attachment" in disp
    assert "preview-invoice.pdf" in disp
    assert PaymentEvent.query.count() == before


def test_editor_page_has_preview_button(ctx, admin_client):
    """The editor's second submit button posts the SAME form to the preview
    route (unsaved edits travel), and the pdf_body field exists."""
    resp = admin_client.get("/admin/financial/invoice")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Download preview" in html
    assert "/admin/financial/document/preview" in html
    assert 'name="pdf_body"' in html


# --- storage posture: the render root is not web-served ----------------------

def test_render_root_not_under_static(ctx):
    # var/doc-render lives under the project root, never under app/static;
    # Flask only maps /static, so job dirs are unreachable over HTTP.
    root = str(_doc_render_root().resolve())
    static = os.path.join(str(ctx.root_path), "static")
    assert "static" not in root
    assert not root.startswith(static)
