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
import threading
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


class PregenBusy(Exception):
    """A warm pregen compile for this kind is already running. Raised when a
    preview needs that pregen mid-flight so the caller can ask the user to retry
    in a few seconds instead of piling on a second compile of the same kind."""


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


def _expand_body(raw_body, vars_: dict) -> str:
    """Expand `{var}` placeholders inside the template's pdf_body, escaping the
    literal text and each substituted value INDEPENDENTLY.

    The whole-body-then-escape approach is wrong once a value can be RawLatex:
    a `\\textbf{...}` placeholder substituted in would itself be escaped into
    literal text. So we split the body on `{known_var}` occurrences, latex_escape
    the literal segments, and insert each value escaped-or-raw by its
    RawLatex-ness (same rule as `esc()`), so a bold-placeholder stays live LaTeX
    while the admin's surrounding prose is neutralised. A RawLatex body as a
    whole still bypasses escaping entirely (the test/preview escape hatch)."""
    if isinstance(raw_body, RawLatex):
        return _render(raw_body, vars_)
    if not raw_body:
        return ""
    if not vars_:
        return latex_escape(raw_body)
    # Longest names first so a prefix key (e.g. "amount") can't shadow a longer
    # one ("amount_ex_gst"); the trailing `}` already disambiguates, but this
    # keeps the alternation order unsurprising.
    names = sorted(vars_, key=len, reverse=True)
    pattern = re.compile(r"\{(" + "|".join(re.escape(n) for n in names) + r")\}")
    out: list[str] = []
    pos = 0
    for m in pattern.finditer(raw_body):
        out.append(latex_escape(raw_body[pos:m.start()]))
        val = vars_[m.group(1)]
        out.append(val if isinstance(val, RawLatex) else latex_escape(str(val)))
        pos = m.end()
    out.append(latex_escape(raw_body[pos:]))
    return "".join(out)


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

    # pdf_body: expand {var} placeholders with per-value escaping so a RawLatex
    # value (e.g. a preview's bold placeholder) survives raw while the literal
    # text around it stays escaped. See `_expand_body`.
    raw_body = tpl.pdf_body or ""
    body_tex = _expand_body(raw_body, v)

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
                    source_date_epoch: int = SOURCE_DATE_EPOCH,
                    template=None) -> bytes:
    """Compile a document to PDF bytes.

    Loads the DocumentTemplate for `kind` (or uses `template`, an unsaved draft
    with the DocumentTemplate fields, so the preview caller can render an
    uncommitted edit without a second code path), assembles the .tex from the
    shared skeleton (escaped structured content), copies any `assets` (`logo` /
    `signature` — referenced by the skeleton only when present), runs tectonic
    with SOURCE_DATE_EPOCH set for byte-reproducibility, and returns the PDF
    bytes. The per-job directory is always removed in a finally.
    """
    from ..models import get_document_template

    assets = assets or {}
    tpl = template if template is not None else get_document_template(kind)
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


# ---------------------------------------------------------------------------
# Preview — a CALLER of render_document, never a second renderer (plan §5).
# ---------------------------------------------------------------------------

# The full variable vocabulary the skeleton consumes (every `esc()` text value
# plus invoice_type, which feeds the invoice title). Every preview variable
# lives here so an unfilled preview shows the field NAMES — including the
# numeric fields, which must never render as a computed $0.00.
_PREVIEW_VARS = (
    "site_name", "business_number", "user_name", "user_email", "recipient_abn",
    "conference_title", "conference_dates", "tier_name", "transaction_id",
    "registration_id", "payment_date", "due_date", "currency_symbol",
    "currency_code", "amount", "gst_amount", "amount_ex_gst",
    "payment_instructions", "payment_link", "invoice_type",
)


def placeholder_vars(kind: str) -> dict[str, RawLatex]:
    """Full variable dict for a preview: every value a bold `\\textbf{name}`
    placeholder (the name itself LaTeX-escaped). Every variable is set to a
    truthy RawLatex, so the optional skeleton sections all appear showing their
    field names — and the numeric fields (amount / gst_amount / amount_ex_gst)
    read as their bold names, never a computed $0.00. Callers overlay real
    values on top."""
    return {name: RawLatex(r"\textbf{" + latex_escape(name) + "}")
            for name in _PREVIEW_VARS}


def preview_document(kind: str, overrides: dict | None = None,
                     template=None) -> bytes:
    """Render a preview PDF for `kind`. A CALLER of `render_document`: it fills
    every variable with a bold-placeholder name, overlays any real `overrides`
    the editor supplied, and compiles via the one renderer. Writes NOTHING — no
    PaymentEvent, no DB row, ever. `template` optionally supplies an unsaved
    draft (a DocumentTemplate-shaped object) so an uncommitted edit can be
    previewed without a second code path."""
    vars_ = placeholder_vars(kind)
    if overrides:
        vars_.update(overrides)
    return render_document(kind, vars_, template=template)


# --- Warm pregen cache: one long-lived PDF per kind, keyed by content_hash. --

# Per-kind collision lock (plan §5). Guards warm generation so two warms never
# race and a preview never spawns a second compile for a kind already warming.
_pregen_locks: dict[str, threading.Lock] = {}
_pregen_locks_guard = threading.Lock()


def _pregen_lock(kind: str) -> threading.Lock:
    with _pregen_locks_guard:
        lock = _pregen_locks.get(kind)
        if lock is None:
            lock = _pregen_locks[kind] = threading.Lock()
        return lock


def _pregen_dir() -> Path:
    return _doc_render_root() / "pregen"


def _pregen_path(kind: str, content_hash: str) -> Path:
    return _pregen_dir() / f"{kind}-{content_hash}.pdf"


def warm_pregen(kind: str) -> Path:
    """Compile the all-placeholders preview for the SAVED template and cache it
    atomically at `var/doc-render/pregen/<kind>-<content_hash>.pdf`, removing any
    stale pregen files for that kind. Held under the per-kind lock; if a warm for
    this kind is already running, raises PregenBusy rather than compiling twice.
    """
    from ..models import get_document_template

    lock = _pregen_lock(kind)
    if not lock.acquire(blocking=False):
        raise PregenBusy(kind)
    try:
        content_hash = get_document_template(kind).content_hash
        pdf = preview_document(kind)          # saved template, all placeholders
        d = _pregen_dir()
        d.mkdir(parents=True, exist_ok=True)
        dest = _pregen_path(kind, content_hash)
        # tmp + rename so a reader never sees a half-written file.
        fd, tmp = tempfile.mkstemp(prefix=f".{kind}-", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(pdf)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        # Drop stale hashes for this kind now the current one is in place.
        for old in d.glob(f"{kind}-*.pdf"):
            if old != dest:
                old.unlink(missing_ok=True)
        return dest
    finally:
        lock.release()


def get_pregen(kind: str) -> bytes:
    """Return the cached warm-pregen bytes for the SAVED template, generating
    them if absent. Raises PregenBusy when the pregen is missing AND a warm
    compile for this kind is already running — so the caller tells the user to
    retry rather than starting a competing compile."""
    from ..models import get_document_template

    dest = _pregen_path(kind, get_document_template(kind).content_hash)
    if dest.exists():
        return dest.read_bytes()
    warm_pregen(kind)                         # raises PregenBusy if lock is held
    return dest.read_bytes()


def preview_pdf(kind: str, overrides: dict | None = None,
                template=None) -> bytes:
    """Apply the serve-vs-recompile rule (plan §5) and return preview PDF bytes.

    Serve the warm pregen IFF no override variable is set AND the (possibly
    draft) template's content matches the saved template (content_hash). Any
    override, or an edited/unsaved body whose hash differs, recompiles fresh via
    `preview_document` (bytes returned, nothing persists — the job dir is deleted
    in render_document's finally). Raises PregenBusy if the pregen must be served
    but a warm compile for the kind is mid-flight."""
    from ..models import get_document_template

    saved_hash = get_document_template(kind).content_hash
    draft_hash = template.content_hash if template is not None else saved_hash
    if not overrides and draft_hash == saved_hash:
        return get_pregen(kind)
    return preview_document(kind, overrides, template=template)


def warm_pregen_async(app, kind: str) -> None:
    """Warm `kind`'s pregen on a daemon thread so the request/boot stays snappy.
    Resilient: PregenBusy (a warm already running) and any compile error are
    swallowed with a log — a failed warm just means the next preview recompiles.
    Skipped under TESTING so the suite never fires background tectonic compiles.
    Step 5 will move this onto the shared compile queue."""
    if app.config.get("TESTING"):
        return

    def _run():
        with app.app_context():
            try:
                warm_pregen(kind)
            except PregenBusy:
                pass
            except Exception:
                app.logger.warning("Pregen warm failed for %s", kind,
                                   exc_info=True)

    threading.Thread(target=_run, name=f"pregen-{kind}", daemon=True).start()
