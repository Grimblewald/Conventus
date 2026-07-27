"""Tests for the one document renderer (app/services/documents.py).

tectonic is a hard dependency, so these compile for real — there is no
skip-if-missing path. Kept to ~8 compiles total (each ~1-3s warm).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
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
    assert not [p for p in root.iterdir() if not p.name.startswith('.')]


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
    assert not [p for p in _doc_render_root().iterdir()
               if not p.name.startswith('.')]


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


def test_preview_shows_the_real_issuer_not_a_placeholder(ctx):
    """Issuer facts are configured once and identical on every document, so a
    preview renders them for real — the rule the letterhead images and the GST
    treatment already follow. Showing a bold `business_legal_name` where the
    admin just saved their society's name reads as "my settings didn't take"."""
    from app.extensions import db
    from app.models import get_document_template, get_financial_identity
    from app.services.documents import assemble_tex, placeholder_vars

    ident = get_financial_identity()
    ident.legal_name = "Example Society Incorporated"
    ident.abn = "12 345 678 901"
    ident.address = "PO Box 1\nAdelaide SA 5000"
    ident.signatory_name = "A. Treasurer"
    ident.signatory_role = "Director/Treasurer"
    db.session.commit()

    tex = assemble_tex("receipt", get_document_template("receipt"),
                       placeholder_vars("receipt"))
    for real in ("Example Society Incorporated", "12 345 678 901",
                 "Adelaide SA 5000", "A. Treasurer", "Director/Treasurer"):
        assert real in tex, real
    for placeholder in (r"\textbf{business\_legal\_name}",
                        r"\textbf{business\_number}",
                        r"\textbf{signatory\_name}"):
        assert placeholder not in tex, placeholder

    # Per-document values are still placeholders — those really are unfilled.
    assert r"\textbf{amount}" in tex
    assert r"\textbf{user\_name}" in tex


def test_preview_keeps_placeholders_for_unset_issuer_fields(ctx):
    """An issuer field the admin has not filled in yet must still show its
    bold name, so the preview says what is missing rather than a silent gap."""
    from app.extensions import db
    from app.models import get_document_template, get_financial_identity
    from app.services.documents import assemble_tex, placeholder_vars

    ident = get_financial_identity()
    ident.signatory_name = ""
    ident.signatory_role = ""
    db.session.commit()

    tex = assemble_tex("invoice", get_document_template("invoice"),
                       placeholder_vars("invoice"))
    assert r"\textbf{signatory\_name}" in tex


def test_replacing_a_letterhead_image_rekeys_the_cache(ctx, tmp_path, monkeypatch):
    """Assets live at fixed paths (logo.png), so the filename alone cannot key
    the cache — replacing the image left the name identical and every cached
    document kept rendering the old picture.

    Runs against an isolated assets dir: writing into the real one would leave
    a non-PNG behind and break every later test that actually compiles.
    """
    import os
    from app.extensions import db
    from app.models import get_document_template, get_financial_identity

    monkeypatch.setattr("app.services.documents.financial_assets_dir",
                        lambda: tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"first-image-bytes")

    ident = get_financial_identity()
    original = ident.logo_filename
    ident.logo_filename = "logo.png"
    db.session.commit()
    try:
        before = get_document_template("invoice").content_hash

        # Same filename, different picture — what the old key was blind to.
        logo.write_bytes(b"a-completely-different-and-longer-image")
        os.utime(logo, (0, 0))

        assert get_document_template("invoice").content_hash != before
    finally:
        ident.logo_filename = original
        db.session.commit()


def test_preview_title_is_not_double_escaped(ctx):
    """The invoice title comes from {invoice_type}, a bold placeholder in a
    preview. Escaping it blindly printed the markup — and document.tex wraps
    the title in \\MakeUppercase, so it surfaced as \\TEXTBF{INVOICE\\_TYPE}."""
    from app.models import get_document_template
    from app.services.documents import assemble_tex, placeholder_vars

    tex = assemble_tex("invoice", get_document_template("invoice"),
                       placeholder_vars("invoice"))
    assert r"\textbackslash{}textbf" not in tex
    assert r"\MakeUppercase{\textbf{invoice\_type}}" in tex


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


def test_document_preview_rejects_unknown_kind(ctx, admin_client, monkeypatch):
    """An unknown kind must be rejected cleanly, not crash in the seed getter
    (regression: kind reached _DOCUMENT_DEFAULTS[kind] and raised KeyError)."""
    called = []
    monkeypatch.setattr(docs, "render_document",
                        lambda *a, **k: called.append(1) or b"%PDF")
    resp = admin_client.post("/admin/financial/document/preview",
                             data={"kind": "bogus", "pdf_body": ""},
                             follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert called == []          # never reached the renderer


def test_editor_page_has_preview_button(ctx, admin_client):
    """The editor's second submit button posts the SAME form to the preview
    route (unsaved edits travel), and the pdf_body field exists."""
    resp = admin_client.get("/admin/financial/documents/invoice")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Download preview" in html
    assert "/admin/financial/document/preview" in html
    assert 'name="pdf_body"' in html


# --- compile queue (plan §6) -------------------------------------------------
#
# These stub the pure compile step (`docs._compile`) so no real tectonic runs —
# the queue machinery is exercised, not the LaTeX toolchain. A tiny draft
# template keeps render_document off the DB so worker/thread contexts stay
# simple.

_DRAFT = types.SimpleNamespace(pdf_body="", gst_registered=False)


def _render_in_thread(app, results, idx, **kw):
    """Run render_document on its own thread inside an app context, stashing the
    (index, exception-or-bytes) into `results`."""
    def _run():
        try:
            with app.app_context():
                out = render_document("invoice", _vars(), template=_DRAFT, **kw)
            results[idx] = out
        except Exception as e:                # noqa: BLE001 - recorded for asserts
            results[idx] = e
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def test_concurrency_cap_serialises(app, monkeypatch):
    """With DOC_COMPILE_WORKERS=1, two concurrent renders never compile at the
    same time — the second's compile starts only after the first's ends."""
    app.config["DOC_COMPILE_WORKERS"] = 1
    intervals = []
    lock = threading.Lock()

    def slow_compile(tectonic, job_dir, tex_path, epoch, memory_mb=0, should_abort=None):
        start = time.monotonic()
        time.sleep(0.15)
        end = time.monotonic()
        with lock:
            intervals.append((start, end))
        return b"%PDF-fake"

    monkeypatch.setattr(docs, "_compile", slow_compile)

    results: dict[int, object] = {}
    t1 = _render_in_thread(app, results, 0)
    t2 = _render_in_thread(app, results, 1)
    t1.join(10); t2.join(10)

    assert results[0] == b"%PDF-fake"
    assert results[1] == b"%PDF-fake"
    # No overlap: one interval must sit entirely after the other.
    (a0, a1), (b0, b1) = sorted(intervals)
    assert a1 <= b0 + 0.01, f"compiles overlapped: {intervals}"


def test_backlog_reports_position(app, monkeypatch):
    """compile_backlog() counts running + queued jobs, so a caller can report
    its position. Uses a gate so jobs pile up deterministically."""
    app.config["DOC_COMPILE_WORKERS"] = 1
    gate = threading.Event()

    def gated_compile(tectonic, job_dir, tex_path, epoch, memory_mb=0, should_abort=None):
        gate.wait(5)
        return b"%PDF-fake"

    monkeypatch.setattr(docs, "_compile", gated_compile)
    assert docs.compile_backlog() == 0

    results: dict[int, object] = {}
    t1 = _render_in_thread(app, results, 0)   # worker picks it up, blocks on gate
    _wait_until(lambda: docs.compile_backlog() == 1)
    t2 = _render_in_thread(app, results, 1)   # queued behind the running one
    _wait_until(lambda: docs.compile_backlog() == 2)

    gate.set()
    t1.join(10); t2.join(10)
    assert results[0] == b"%PDF-fake" and results[1] == b"%PDF-fake"
    _wait_until(lambda: docs.compile_backlog() == 0)


def test_queue_survives_a_raising_job(app, monkeypatch):
    """A job whose compile raises surfaces a RenderError but does NOT kill the
    worker — a following render still succeeds."""
    app.config["DOC_COMPILE_WORKERS"] = 1
    state = {"boom": True}

    def flaky_compile(tectonic, job_dir, tex_path, epoch, memory_mb=0, should_abort=None):
        if state["boom"]:
            state["boom"] = False
            raise ValueError("kaboom")
        return b"%PDF-ok"

    monkeypatch.setattr(docs, "_compile", flaky_compile)

    with app.app_context():
        with pytest.raises(RenderError) as ei:
            render_document("invoice", _vars(), template=_DRAFT)
        assert "kaboom" in str(ei.value)
        # Worker is still alive and serving.
        assert render_document("invoice", _vars(), template=_DRAFT) == b"%PDF-ok"


def test_render_document_cleans_job_dir_via_queue(app, monkeypatch):
    """Behaviour unchanged: bytes out, job dir removed in finally, even though
    the compile now happens on a worker."""
    monkeypatch.setattr(docs, "_compile",
                        lambda *a, **k: b"%PDF-queued")
    with app.app_context():
        out = render_document("invoice", _vars(), template=_DRAFT)
    assert out == b"%PDF-queued"
    assert not [p for p in _doc_render_root().iterdir()
               if not p.name.startswith('.')]


def test_queued_preview_route_flashes_position_and_does_not_block(
        ctx, admin_client, monkeypatch):
    """When the queue is backed up, the preview route reports the position and
    returns immediately WITHOUT compiling (never blocks the request)."""
    monkeypatch.setattr(docs, "compile_backlog", lambda: 2)
    called = []
    monkeypatch.setattr(docs, "preview_pdf",
                        lambda *a, **k: called.append(1) or b"%PDF")

    resp = admin_client.post("/admin/financial/document/preview",
                             data={"kind": "invoice"}, follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "queued" in body and "position" in body
    assert called == []                       # never blocked on a compile


def _wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


# --- storage posture: the render root is not web-served ----------------------

def test_render_root_not_under_static(ctx):
    # var/doc-render lives under the project root, never under app/static;
    # Flask only maps /static, so job dirs are unreachable over HTTP.
    root = str(_doc_render_root().resolve())
    static = os.path.join(str(ctx.root_path), "static")
    assert "static" not in root
    assert not root.startswith(static)


# --- deploy health probe (plan §7/§11): "tectonic absent/unhealthy must be
# LOUD" — a cheap status check, never a compile, for admin/deploy surfacing. --

def test_tectonic_health_false_when_binary_missing(ctx, monkeypatch):
    monkeypatch.setitem(ctx.config, "TECTONIC_BIN", "/nonexistent/tectonic-xyz")
    ok, reason = docs.tectonic_health()
    assert ok is False
    assert "not found" in reason


def test_tectonic_health_true_when_binary_present(ctx):
    ok, reason = docs.tectonic_health()
    assert ok is True
    assert "tectonic" in reason


def test_tectonic_health_notes_cold_pregen(ctx):
    # _clean_render_root wipes var/doc-render (including any pregen) before
    # each test, so with nothing warmed yet the probe still reports healthy
    # (the binary IS there) but flags the cache as not warm.
    ok, reason = docs.tectonic_health()
    assert ok is True
    assert "not yet warmed" in reason


def test_tectonic_health_notes_warm_pregen(ctx):
    pregen_dir = docs._pregen_dir()
    pregen_dir.mkdir(parents=True, exist_ok=True)
    (pregen_dir / "invoice-deadbeef.pdf").write_bytes(b"%PDF-fake")
    ok, reason = docs.tectonic_health()
    assert ok is True
    assert "pregen warm" in reason


# --- Resource safety: a small VPS runs several gunicorn workers --------------
#
# Regression cover for the outage of 2026-07-22: every gunicorn worker ran the
# app factory, each warmed three document kinds, and the resulting concurrent
# tectonic processes tripped the OOM killer — which took gunicorn down, and
# systemd restarted it straight back into the same loop.

def test_boot_warm_is_off_by_default(app):
    """Boot-time warming must stay opt-in: the app factory runs in EVERY
    gunicorn worker, so a boot compile multiplies by worker count."""
    from app.config import BaseConfig
    assert BaseConfig.DOC_WARM_ON_BOOT is False


def test_boot_warm_respects_the_flag(monkeypatch):
    """_warm_document_pregen must not spawn compiles unless explicitly enabled."""
    import sys as _sys
    from app import _warm_document_pregen

    started: list[str] = []
    monkeypatch.setattr("app.services.documents.warm_pregen_async",
                        lambda app_, kind: started.append(kind))
    # The real guard also skips under pytest; bypass it so the flag is what we
    # are actually asserting on.
    monkeypatch.setitem(_sys.modules, "pytest", None)
    monkeypatch.delitem(_sys.modules, "pytest")

    class _App:
        def __init__(self, **cfg):
            self.config = cfg

    _warm_document_pregen(_App(DOC_WARM_ON_BOOT=False, TESTING=False))
    assert started == []


def test_compile_takes_a_box_wide_lock(ctx, monkeypatch):
    """One tectonic per machine, not per worker: the in-process queue caps
    concurrency inside a single gunicorn worker only."""
    from app.services import documents as docs

    taken: list[str] = []
    real_lock = docs._box_compile_lock

    def _spy():
        taken.append("locked")
        return real_lock()

    monkeypatch.setattr(docs, "_box_compile_lock", _spy)
    pdf = render_document("invoice", _vars())
    assert pdf[:4] == b"%PDF"
    assert taken == ["locked"]


def test_box_lock_is_exclusive_across_processes():
    """The lock is a real flock, so a second holder blocks — that is what makes
    it work across gunicorn workers rather than only across threads."""
    import fcntl
    import os
    from app.services import documents as docs

    fd = docs._box_compile_lock()
    try:
        probe = os.open(str(docs._doc_render_root() / ".compile.lock"),
                        os.O_CREAT | os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_compile_runs_under_a_memory_cap(ctx):
    """A capped child dies on its own instead of letting the kernel OOM killer
    pick a victim (which on this box was a gunicorn worker)."""
    from app.services.documents import _memory_limiter, _compile_memory_mb

    assert _compile_memory_mb() > 0            # always capped by default
    assert _memory_limiter(0) is None          # 0 disables
    assert callable(_memory_limiter(512))
    # The cap must not break an ordinary document.
    assert render_document("invoice", _vars())[:4] == b"%PDF"


def test_memory_cap_is_enforced_on_the_child(ctx, monkeypatch):
    """The limiter is really applied to the subprocess — proved by setting a
    cap so small that the compile cannot possibly succeed."""
    monkeypatch.setitem(ctx.config, "DOC_COMPILE_MEMORY_MB", 16)
    with pytest.raises(RenderError):
        render_document("invoice", _vars())
    # A failed compile still cleans up after itself.
    assert not [p for p in _doc_render_root().iterdir()
                if not p.name.startswith('.')]


def test_memory_cap_never_drops_below_what_tectonic_needs(monkeypatch):
    """The host share may RAISE the cap, never lower it below the floor.

    Scaling the cap down on a small machine looks frugal and is in fact fatal:
    tectonic needs a roughly constant address space to start at all, so a
    proportional cap on a 512MB Pi (0.4 × 512 = 204MB) does not render smaller
    documents, it fails every document. Compiles are already serialised
    box-wide, so the cap's real job is bounding a runaway — not squeezing
    tectonic below its working set.
    """
    from app.services import documents as docs

    for total in (256, 512, 1024, 0):     # 0 = RAM undetectable
        monkeypatch.setattr(docs, "total_memory_mb", lambda t=total: t)
        assert docs.auto_memory_mb() == docs.MIN_COMPILE_MEMORY_MB, total

    # A roomier host gets a proportionally larger allowance…
    monkeypatch.setattr(docs, "total_memory_mb", lambda: 4096)
    assert docs.auto_memory_mb() > docs.MIN_COMPILE_MEMORY_MB

    # …but one compile can never dominate a big box either.
    monkeypatch.setattr(docs, "total_memory_mb", lambda: 32000)
    assert docs.auto_memory_mb() == docs.MAX_COMPILE_MEMORY_MB


def test_a_document_really_compiles_under_a_small_host_cap(ctx, monkeypatch):
    """The one that would have caught the bug: derive the cap as a 512MB Pi
    would, then render a real document under it. An assertion about the number
    alone cannot tell you whether tectonic can live inside it."""
    from app.services import documents as docs

    monkeypatch.setattr(docs, "total_memory_mb", lambda: 512)
    monkeypatch.setitem(ctx.config, "DOC_COMPILE_MEMORY_MB", 0)   # derive
    assert render_document("invoice", _vars())[:4] == b"%PDF"


def test_explicit_memory_config_overrides_the_host_default(ctx, monkeypatch):
    from app.services.documents import _compile_memory_mb
    monkeypatch.setitem(ctx.config, "DOC_COMPILE_MEMORY_MB", 333)
    assert _compile_memory_mb() == 333


def test_total_memory_is_detected(ctx):
    """The auto cap is only meaningful if RAM detection works here."""
    from app.services.documents import total_memory_mb
    assert total_memory_mb() > 0


def test_concurrent_renders_never_overlap_across_threads(ctx):
    """The box-wide lock must hold under real concurrency: overlapping
    compiles are exactly what exhausted memory in production."""
    import concurrent.futures

    active = []
    peak = []
    guard = threading.Lock()
    real = docs._compile

    def _watched(tectonic, job_dir, tex_path, epoch, memory_mb=0, should_abort=None):
        with guard:
            active.append(1)
            peak.append(len(active))
        try:
            time.sleep(0.05)
            return real(tectonic, job_dir, tex_path, epoch, memory_mb, should_abort)
        finally:
            with guard:
                active.pop()

    def _render_one():
        # Each pool thread needs its own app context — render_document reads
        # the template and config from it.
        with ctx.app_context():
            return render_document("invoice", _vars())

    docs._compile = _watched
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_render_one) for _ in range(4)]
            results = [f.result() for f in futures]
    finally:
        docs._compile = real

    assert all(r[:4] == b"%PDF" for r in results)
    assert max(peak) == 1, f"compiles overlapped: peak {max(peak)}"


# --- Document layout: each kind must say the right thing --------------------
#
# The skeleton carries every kind's block and selects between them with LaTeX
# conditionals, so the flags — not the presence of a label in the source — are
# what decide the output. Asserting the flags is exact and needs no compile;
# the real compiles above prove the flagged source builds.

def _tex(kind, ctx, **over):
    from app.models import get_document_template
    return assemble_tex(kind, get_document_template(kind), _vars(**over))


def test_kind_flags_select_the_right_document(ctx):
    inv = _tex("invoice", ctx)
    assert r"\isinvoicetrue" in inv
    assert r"\isreceiptfalse" in inv and r"\isadjustmentfalse" in inv

    rec = _tex("receipt", ctx)
    assert r"\isreceipttrue" in rec
    assert r"\isinvoicefalse" in rec and r"\isadjustmentfalse" in rec

    adj = _tex("adjustment", ctx)
    assert r"\isadjustmenttrue" in adj
    assert r"\isinvoicefalse" in adj and r"\isreceiptfalse" in adj


def test_invoice_shows_how_to_pay(ctx):
    """Pay-online and bank details are switched on only when there is
    something to show, and only an invoice asks for money."""
    with_pay = _tex("invoice", ctx, payment_link="https://example.org/pay/X",
                    payment_instructions="BSB 000-000")
    assert r"\paylinktrue" in with_pay and r"\payinstrtrue" in with_pay

    without = _tex("invoice", ctx, payment_link="", payment_instructions="")
    assert r"\paylinkfalse" in without and r"\payinstrfalse" in without


def test_gst_registered_shows_the_breakdown(ctx):
    tex = _tex("invoice", ctx, gst_applies="1", invoice_type="Tax Invoice")
    assert r"\gsttrue" in tex
    assert "Tax Invoice" in tex


def test_not_gst_registered_states_it_plainly(ctx):
    """The reference document this was modelled on omits any GST statement,
    which leaves the payer guessing; a zero-valued GST line would be worse
    still, implying a taxable sale that was not taxed. So the skeleton carries
    an explicit statement — and when the issuer genuinely is not registered, it
    names them."""
    tex = _tex("invoice", ctx, gst_applies="", gst_registered="",
               invoice_type="Invoice",
               business_legal_name="Example Society Ltd")
    assert r"\gstfalse" in tex and r"\gstregfalse" in tex
    flat = " ".join(tex.split())
    assert "No GST has been charged. Example Society Ltd is not registered "\
           "for GST." in flat


def test_a_registered_issuer_never_claims_to_be_unregistered(ctx):
    """"No GST on this sale" and "not registered for GST" are different facts.

    A GST-registered society can legitimately issue a GST-free invoice (an
    overseas sponsor, say) by unticking the per-send toggle. Printing a
    non-registration statement on that document would be a false statement on
    a tax document, so the two travel as separate flags.
    """
    tex = _tex("invoice", ctx, gst_applies="", gst_registered="1",
               invoice_type="Invoice",
               business_legal_name="Example Society Ltd")
    assert r"\gstfalse" in tex, "no GST was charged on this sale"
    assert r"\gstregtrue" in tex, "but the issuer IS registered"


def test_all_three_tax_statements_actually_compile(ctx):
    """The tax statement is a nested LaTeX conditional; a miscounted \\fi is
    invisible in the source assertions above and fatal at compile time."""
    for gst, reg in (("1", "1"), ("", "1"), ("", "")):
        pdf = render_document("invoice", _vars(gst_applies=gst,
                                               gst_registered=reg))
        assert pdf[:4] == b"%PDF", (gst, reg)


def test_legacy_documents_without_the_registration_flag_still_regenerate(ctx):
    """Snapshots issued before the two facts were split carry only
    gst_applies. Falling back to it reproduces exactly what was issued."""
    tex = _tex("invoice", ctx, gst_applies="")
    assert r"\gstregfalse" in tex
    charged = _tex("invoice", ctx, gst_applies="1")
    assert r"\gstregtrue" in charged


def test_issuer_and_signatory_appear(ctx):
    tex = _tex("receipt", ctx, business_number="17 602 379 475",
               business_address="PO Box 1\nAdelaide SA 5000",
               signatory_name="Tim Barnes", signatory_role="Director/Treasurer",
               business_legal_name="Example Society Ltd")
    assert "17 602 379 475" in tex
    assert "Adelaide SA 5000" in tex
    assert "Tim Barnes" in tex
    assert "Director/Treasurer" in tex
    assert r"\signatorytrue" in tex


def test_optional_blocks_vanish_when_unset(ctx):
    """Empty values must switch their block off, not print an empty heading."""
    tex = _tex("receipt", ctx, business_number="", business_address="",
               signatory_name="", signatory_role="", recipient_abn="",
               recipient_address="")
    for flag in (r"\bnumfalse", r"\baddrfalse", r"\signatoryfalse",
                 r"\rabnfalse", r"\raddrfalse"):
        assert flag in tex, flag


def test_abandoned_job_skips_compile_after_acquiring_lock(ctx):
    """A job whose caller has already timed out must not spend the single
    machine-wide compile slot: once the box lock is held, an abandoned job is
    skipped instead of running tectonic (regression for the box-lock backlog)."""
    from app.services.documents import _CompileJob, _compile

    ran = []
    real = docs._compile

    def _counting(*a, **k):
        ran.append(1)
        return real(*a, **k)

    docs._compile = _counting
    try:
        # A job flagged abandoned before it executes must not compile.
        job = _CompileJob("tectonic", _doc_render_root(), _doc_render_root() / "x.tex",
                          1704067200, 0)
        job.abandoned = True
        job.execute()
    finally:
        docs._compile = real

    # _compile was entered, but should_abort short-circuited before tectonic ran.
    assert isinstance(job.error, RenderError)
    assert "abandoned" in str(job.error)


def test_should_abort_predicate_prevents_tectonic_run(ctx, monkeypatch):
    """_compile checks should_abort right after taking the lock, before it
    would launch the subprocess."""
    launched = []
    monkeypatch.setattr(docs.subprocess, "run",
                        lambda *a, **k: launched.append(1))
    with pytest.raises(RenderError) as ei:
        docs._compile("tectonic", _doc_render_root(),
                      _doc_render_root() / "x.tex", 1704067200,
                      should_abort=lambda: True)
    assert "abandoned" in str(ei.value)
    assert launched == []
