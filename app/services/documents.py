"""The one document renderer.

Compiles invoice / receipt / adjustment-note PDFs from a trusted, in-repo
LaTeX skeleton (`app/latex/document.tex`) using tectonic. Option A: admins
never author raw LaTeX — every structured value is LaTeX-escaped before it
reaches the skeleton, so there is no `\\input`/macro-bomb surface and no OS
sandbox is needed. This module is the single compile code path; preview and
send (later build steps) are callers of `render_document`, never re-implementations.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import current_app

# Fixed epoch for byte-reproducible PDFs (tectonic honours SOURCE_DATE_EPOCH).
# 2024-01-01T00:00:00Z. Never change casually — the regeneration store (plan
# §12) relies on identical inputs producing identical bytes across releases.
SOURCE_DATE_EPOCH = 1704067200

# Compile wall-clock ceiling. A warm cache compiles in ~1-2s; a cold fetch is
# still comfortably inside this.
_COMPILE_TIMEOUT = 60

_SKELETON = Path(__file__).resolve().parent.parent / "latex" / "document.tex"


class RenderError(Exception):
    """A compile (or discovery) failure. `.log` carries a trimmed tail of the
    tectonic output so callers can surface it inline."""

    def __init__(self, message: str, *, log: str = "") -> None:
        super().__init__(message)
        self.log = log


class RawLatex(str):
    """Marker for a value that must be injected WITHOUT escaping. The renderer
    itself never mints raw values from admin input; it is the escape hatch a
    later preview step uses to inject `\\textbf{...}` placeholders."""


def latex_escape(s: str) -> str:
    """Escape text for LaTeX text mode.

    Character handling matches the abstract-booklet export (this is the single
    home for that table now — `admin/conferences.py` imports it from here).
    Newlines additionally become forced line breaks: a blank line starts a new
    paragraph, a single newline is a `\\\\` break, and leading/trailing blank
    lines are dropped so no break is ever emitted with nothing to break.
    """
    s = str(s)
    # Backslash goes through a sentinel so the braces of \textbackslash{}
    # survive the brace escaping below (same trick as the booklet export).
    _bsl = "\x00BSL\x00"
    s = s.replace("\\", _bsl)
    s = s.replace("&", "\\&")
    s = s.replace("%", "\\%")
    s = s.replace("$", "\\$")
    s = s.replace("#", "\\#")
    s = s.replace("_", "\\_")
    s = s.replace("{", "\\{")
    s = s.replace("}", "\\}")
    s = s.replace("~", "\\textasciitilde{}")
    s = s.replace("^", "\\^{}")
    s = s.replace(_bsl, "\\textbackslash{}")

    s = s.replace("\r\n", "\n").replace("\r", "\n")
    paras = [p for p in re.split(r"\n[ \t]*\n", s) if p.strip()]
    paras = [p.strip("\n").replace("\n", " \\\\\n") for p in paras]
    return "\n\n".join(paras)


def _render(template: str, vars_: dict) -> str:
    """Plain `{key}` placeholder substitution (same contract as the email
    templates in services/invoice.py)."""
    for key, val in vars_.items():
        template = template.replace("{" + key + "}", str(val))
    return template


def _flag(name: str, on: bool) -> RawLatex:
    return RawLatex(f"\\{name}{'true' if on else 'false'}")


def assemble_tex(kind: str, tpl, vars_: dict, *, has_logo: bool = False,
                 has_signature: bool = False, logo_name: str = "",
                 signature_name: str = "") -> str:
    """Build the compile-ready .tex source. Separated from `render_document`
    so the generated source is testable without invoking tectonic."""
    v = dict(vars_)
    gst_on = bool(getattr(tpl, "gst_registered", False))

    # Title reflects the kind; the invoice kind defers to the GST-aware
    # {invoice_type} value ("Tax Invoice" vs "Invoice").
    doc_title = {"receipt": "Receipt", "adjustment": "Adjustment Note"}.get(
        kind, v.get("invoice_type") or "Invoice")

    # pdf_body: expand {var} placeholders, then escape — unless the caller
    # handed a RawLatex body (the preview/test escape hatch).
    raw_body = tpl.pdf_body or ""
    body_expanded = _render(raw_body, v)
    body_tex = (body_expanded if isinstance(raw_body, RawLatex)
                else latex_escape(body_expanded))

    def esc(key: str) -> str:
        val = v.get(key, "")
        return val if isinstance(val, RawLatex) else latex_escape(str(val))

    subst = {
        "flag_gst": _flag("gst", gst_on),
        "flag_logo": _flag("haslogo", has_logo),
        "flag_signature": _flag("hassig", has_signature),
        "flag_recipient_abn": _flag("rabn", bool(v.get("recipient_abn"))),
        "flag_due_date": _flag("duedate", bool(v.get("due_date"))),
        "flag_business_number": _flag("bnum", bool(v.get("business_number"))),
        "flag_payment_instructions": _flag("payinstr", bool(v.get("payment_instructions"))),
        "flag_payment_link": _flag("paylink", bool(v.get("payment_link"))),
        "flag_pdf_body": _flag("pdfbody", bool(raw_body)),
        "logo_file": RawLatex(logo_name),
        "signature_file": RawLatex(signature_name),
        "doc_title": latex_escape(doc_title),
        "site_name": esc("site_name"),
        "business_number": esc("business_number"),
        "user_name": esc("user_name"),
        "user_email": esc("user_email"),
        "recipient_abn": esc("recipient_abn"),
        "conference_title": esc("conference_title"),
        "conference_dates": esc("conference_dates"),
        "tier_name": esc("tier_name"),
        "transaction_id": esc("transaction_id"),
        "registration_id": esc("registration_id"),
        "payment_date": esc("payment_date"),
        "due_date": esc("due_date"),
        "currency_symbol": esc("currency_symbol"),
        "currency_code": esc("currency_code"),
        "amount": esc("amount"),
        "gst_amount": esc("gst_amount"),
        "amount_ex_gst": esc("amount_ex_gst"),
        "payment_instructions": esc("payment_instructions"),
        "payment_link": esc("payment_link"),
        "pdf_body": body_tex,
    }

    skeleton = _SKELETON.read_text(encoding="utf-8")
    # Single-pass replacement so an escaped value that happens to look like a
    # token (e.g. a user typing "@@amount@@") is never itself substituted.
    return re.sub(r"@@(\w+)@@",
                  lambda m: str(subst.get(m.group(1), m.group(0))),
                  skeleton)


def _doc_render_root() -> Path:
    """Project temp root for job dirs. Under `var/`, never web-served (Flask
    only maps /static) and .gitignored. Config key `DOC_RENDER_ROOT` overrides
    the `<project>/var/doc-render` default."""
    root = None
    try:
        root = current_app.config.get("DOC_RENDER_ROOT")
    except RuntimeError:
        pass
    if not root:
        project_root = Path(__file__).resolve().parent.parent.parent
        root = project_root / "var" / "doc-render"
    return Path(root)


def _resolve_tectonic() -> str:
    """Locate the tectonic binary. `TECTONIC_BIN` (default "tectonic") is
    resolved via PATH; the default name additionally falls back to the
    user-local install. A configured-but-missing binary raises."""
    configured = "tectonic"
    try:
        configured = current_app.config.get("TECTONIC_BIN") or "tectonic"
    except RuntimeError:
        pass

    found = shutil.which(configured)
    if found:
        return found
    if os.path.isabs(configured) and os.access(configured, os.X_OK):
        return configured
    if configured == "tectonic":
        fallback = Path.home() / ".local" / "bin" / "tectonic"
        if fallback.exists() and os.access(fallback, os.X_OK):
            return str(fallback)
    raise RenderError(
        f"tectonic not found (TECTONIC_BIN={configured!r}; checked PATH"
        + (" and ~/.local/bin" if configured == "tectonic" else "") + ")")


def _trim_log(data) -> str:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    return "\n".join((data or "").splitlines()[-40:])


def render_document(kind: str, vars_: dict[str, str],
                    assets: dict[str, Path] | None = None, *,
                    source_date_epoch: int = SOURCE_DATE_EPOCH) -> bytes:
    """Compile a document to PDF bytes.

    Loads the DocumentTemplate for `kind`, assembles the .tex from the shared
    skeleton (escaped structured content), copies any `assets` (`logo` /
    `signature` — referenced by the skeleton only when present), runs tectonic
    with SOURCE_DATE_EPOCH set for byte-reproducibility, and returns the PDF
    bytes. The per-job directory is always removed in a finally.
    """
    from ..models import get_document_template

    assets = assets or {}
    tpl = get_document_template(kind)
    tectonic = _resolve_tectonic()

    root = _doc_render_root()
    root.mkdir(parents=True, exist_ok=True)
    job = Path(tempfile.mkdtemp(prefix="job-", dir=str(root)))
    try:
        has_logo = "logo" in assets
        has_sig = "signature" in assets
        logo_name = signature_name = ""
        if has_logo:
            logo_name = "logo" + Path(assets["logo"]).suffix
            shutil.copy2(assets["logo"], job / logo_name)
        if has_sig:
            signature_name = "signature" + Path(assets["signature"]).suffix
            shutil.copy2(assets["signature"], job / signature_name)

        tex = assemble_tex(kind, tpl, vars_, has_logo=has_logo,
                           has_signature=has_sig, logo_name=logo_name,
                           signature_name=signature_name)
        tex_path = job / "document.tex"
        tex_path.write_text(tex, encoding="utf-8")

        # Network stays ON (a cold cache must still fetch packages).
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(source_date_epoch))
        try:
            proc = subprocess.run(
                [tectonic, "--outdir", str(job), str(tex_path)],
                capture_output=True, timeout=_COMPILE_TIMEOUT,
                env=env, cwd=str(job),
            )
        except subprocess.TimeoutExpired as e:
            raise RenderError(
                f"tectonic timed out after {_COMPILE_TIMEOUT}s",
                log=_trim_log((e.stdout or b"") + (e.stderr or b"")))

        if proc.returncode != 0:
            raise RenderError(
                f"tectonic exited {proc.returncode}",
                log=_trim_log(proc.stdout + proc.stderr))

        pdf = job / "document.pdf"
        if not pdf.exists():
            raise RenderError("tectonic produced no PDF",
                              log=_trim_log(proc.stdout + proc.stderr))
        return pdf.read_bytes()
    finally:
        shutil.rmtree(job, ignore_errors=True)
