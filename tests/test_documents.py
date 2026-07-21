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


# --- storage posture: the render root is not web-served ----------------------

def test_render_root_not_under_static(ctx):
    # var/doc-render lives under the project root, never under app/static;
    # Flask only maps /static, so job dirs are unreachable over HTTP.
    root = str(_doc_render_root().resolve())
    static = os.path.join(str(ctx.root_path), "static")
    assert "static" not in root
    assert not root.startswith(static)
