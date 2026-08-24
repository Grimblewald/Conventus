"""The one document renderer.

Compiles invoice / receipt / adjustment-note PDFs from a trusted, in-repo
LaTeX skeleton (`app/latex/document.tex`) using tectonic. Option A: admins
never author raw LaTeX — every structured value is LaTeX-escaped before it
reaches the skeleton, so there is no `\\input`/macro-bomb surface and no OS
sandbox is needed. This module is the single compile code path; preview and
send (later build steps) are callers of `render_document`, never re-implementations.

Compile queue (plan §6)
-----------------------
tectonic is CPU/RAM-heavy and the VPS is small, so every compile is funnelled
through ONE process-wide queue served by a small daemon worker pool
(`DOC_COMPILE_WORKERS`, default 1). `render_document` splits into two phases:
"prepare" (DB read, LaTeX escaping, job-dir + assets — runs in the caller's
request/app context) and "compile" (pure tectonic run on the prepared job dir —
runs on a worker, touches no DB, needs no app context). There stays exactly one
compile code path: the pure step is `_compile`, and every caller — preview, warm
pregen, boot warm — reaches it through `render_document` → the queue.

Multi-process reality: the queue, worker pool and backlog counter are plain
in-process objects, so they cap concurrency within ONE worker only — and
gunicorn runs several. Because tectonic is the memory hog on a small box, the
compile itself additionally takes a box-wide `flock` (`_box_compile_lock`), so
at most one tectonic runs per machine no matter the worker count, and the child
is capped by `DOC_COMPILE_MEMORY_MB` so a runaway compile dies instead of the
OOM killer choosing a gunicorn worker. Boot-time warming is off by default for
the same reason (`DOC_WARM_ON_BOOT`).
"""
from __future__ import annotations

import fcntl
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

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
    # GST comes from the variables, not the template: the send layer resolves
    # it from the financial identity (or a per-send override) and it is
    # snapshotted, so a regenerated document keeps the treatment it was issued
    # under rather than today's registration status.
    gst_on = bool(v.get("gst_applies"))
    # Registration status is NOT the same fact as "GST charged on this sale".
    # A GST-registered issuer can legitimately issue a GST-free invoice (an
    # overseas sponsor, say); printing "not registered for GST" on it would be
    # a false statement on a tax document. Absent from legacy snapshots, where
    # the two facts were conflated — fall back to gst_on so those regenerate
    # exactly as issued.
    gst_registered = bool(v.get("gst_registered", gst_on))

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
        "flag_gst_registered": _flag("gstreg", gst_registered),
        "flag_logo": _flag("haslogo", has_logo),
        "flag_signature": _flag("hassig", has_signature),
        "flag_recipient_abn": _flag("rabn", bool(v.get("recipient_abn"))),
        "flag_recipient_address": _flag("raddr", bool(v.get("recipient_address"))),
        "flag_due_date": _flag("duedate", bool(v.get("due_date"))),
        "flag_business_number": _flag("bnum", bool(v.get("business_number"))),
        "flag_business_address": _flag("baddr", bool(v.get("business_address"))),
        "flag_contact_email": _flag("bmail", bool(v.get("business_contact_email"))),
        "flag_signatory": _flag("signatory", bool(v.get("signatory_name"))),
        "flag_payment_instructions": _flag("payinstr", bool(v.get("payment_instructions"))),
        "flag_payment_link": _flag("paylink", bool(v.get("payment_link"))),
        "flag_pdf_body": _flag("pdfbody", bool(raw_body)),
        "flag_receipt": _flag("isreceipt", kind == "receipt"),
        "flag_adjustment": _flag("isadjustment", kind == "adjustment"),
        "flag_invoice": _flag("isinvoice", kind == "invoice"),
        "logo_file": RawLatex(logo_name),
        "signature_file": RawLatex(signature_name),
        # RawLatex-aware like every other value: the invoice title comes from
        # {invoice_type}, which in a preview is a bold placeholder. Escaping it
        # blindly printed the markup itself — and document.tex wraps the title
        # in \MakeUppercase, so it surfaced as \TEXTBF{INVOICE\_TYPE}.
        "doc_title": (doc_title if isinstance(doc_title, RawLatex)
                      else latex_escape(str(doc_title))),
        "site_name": esc("site_name"),
        "business_legal_name": esc("business_legal_name"),
        "business_address": esc("business_address"),
        "business_contact_email": esc("business_contact_email"),
        "signatory_name": esc("signatory_name"),
        "signatory_role": esc("signatory_role"),
        "recipient_address": esc("recipient_address"),
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
        # Documents issued before the rate was configurable carry no gst_rate
        # in their snapshot, and a blank would print "GST (%)" on regeneration.
        # They were all taxed at the Australian 10%, so that is what they say.
        "gst_rate": esc("gst_rate") if v.get("gst_rate") else "10",
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


def financial_assets_dir() -> Path:
    """Where the letterhead logo and signature live. Under `var/`, NOT the
    public uploads tree — a signature image is forgeable material and must
    never be web-served; admins preview it through an authenticated route."""
    try:
        configured = current_app.config.get("FINANCIAL_ASSETS_DIR")
    except RuntimeError:
        configured = None
    if not configured:
        project_root = Path(__file__).resolve().parent.parent.parent
        configured = project_root / "var" / "financial-assets"
    return Path(configured)


def identity_assets() -> dict[str, Path]:
    """The financial identity's assets that exist on disk, keyed as the
    renderer expects (`logo`, `signature`)."""
    from ..models import get_financial_identity

    try:
        ident = get_financial_identity()
    except Exception:                     # no DB/app context (e.g. tooling)
        return {}
    root = financial_assets_dir()
    out: dict[str, Path] = {}
    for key, name in (("logo", ident.logo_filename),
                      ("signature", ident.signature_filename)):
        if name and (root / name).is_file():
            out[key] = root / name
    return out


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


def tectonic_health() -> tuple[bool, str]:
    """Cheap status probe for deploy/admin surfacing (plan §7/§11): "tectonic
    absent/unhealthy must be LOUD" since there is no plain-format fallback.

    Resolves the binary via `_resolve_tectonic` (same lookup the renderer
    uses) and, if found, checks whether the warm pregen cache directory
    already holds at least one compiled PDF — a cheap proxy that a compile
    has actually succeeded on this box, without spending a compile on the
    check itself. NEVER compiles inline; this must stay fast enough to call
    on every dashboard render.

    Returns `(False, reason)` when the binary is missing, or `(True, status)`
    when it's present — the status notes whether the pregen cache is warm
    yet (it may legitimately not be, e.g. right after a fresh install before
    boot's background warm has finished)."""
    try:
        path = _resolve_tectonic()
    except RenderError as e:
        return False, str(e)

    pregen_dir = _pregen_dir()
    warm = pregen_dir.is_dir() and any(pregen_dir.glob("*.pdf"))
    if warm:
        return True, f"tectonic {path}; pregen warm"
    return True, f"tectonic {path}; pregen not yet warmed"


def _trim_log(data) -> str:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    return "\n".join((data or "").splitlines()[-40:])


# ---------------------------------------------------------------------------
# Process-wide compile queue (plan §6). Serialises tectonic across the whole
# process at capped concurrency. See the module docstring for the single-process
# rationale.
# ---------------------------------------------------------------------------

# Total wall-clock a request will wait for its PDF (queue wait + compile). Past
# this the caller gives up with a RenderError; the abandoned job is skipped or
# discarded by the worker, keeping the queue consistent. Config override:
# `DOC_COMPILE_TIMEOUT`.
_QUEUE_WAIT_TIMEOUT = 120

_compile_queue: "queue.Queue[_CompileJob]" = queue.Queue()
_running_jobs = 0                      # jobs a worker is actively compiling
_running_guard = threading.Lock()
_workers_started = False
_workers_guard = threading.Lock()


def _memory_limiter(limit_mb: int):
    """preexec_fn capping the child's address space, or None when disabled.

    The cap is the difference between "this compile fails" and "the kernel
    picks something to kill" — and the kernel's choice on a small box tends to
    be a gunicorn worker, not tectonic. Applied to the child only.
    """
    if limit_mb <= 0:
        return None
    import resource

    limit = limit_mb * 1024 * 1024

    def _apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return _apply


def total_memory_mb() -> int:
    """Physical RAM in MB, or 0 when it cannot be determined."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 0


# Address space below which tectonic cannot compile even a trivial document.
# Measured against tectonic 0.16: 448MB and under abort during font/format
# loading ("xmalloc request for 76327568 bytes failed"), 512MB succeeds. The
# floor sits above the measured boundary rather than on it, because a real
# document carries more content than the probe did.
MIN_COMPILE_MEMORY_MB = 640
# One compile is never allowed more than this, however large the host is.
MAX_COMPILE_MEMORY_MB = 2048


def auto_memory_mb() -> int:
    """Memory cap derived from the host — a runaway guard, not a quota.

    The instinct to scale the cap down on a small machine is wrong: tectonic
    needs a roughly constant amount of address space to start at all, so a
    proportional cap on a 512MB Pi (0.4 × 512 = 204MB) does not make documents
    render frugally, it makes every document fail. Since compiles are already
    serialised box-wide by `_box_compile_lock`, at most one of these limits is
    live at a time, and the honest job of the cap is to stop a pathological
    document from expanding without bound — not to fit tectonic into less than
    it can run in. So the host share may only ever raise the cap above the
    known-working floor, never lower it.
    """
    total = total_memory_mb()
    if not total:
        return MIN_COMPILE_MEMORY_MB
    share = int(total * 0.4)
    return max(MIN_COMPILE_MEMORY_MB, min(MAX_COMPILE_MEMORY_MB, share))


def _compile_memory_mb() -> int:
    """The child memory cap, resolved in the caller's context — the compile
    worker thread runs without an app context, so config is unreachable there.
    A configured 0 means "derive from the host" (see `auto_memory_mb`); an
    explicit value overrides."""
    try:
        configured = int(current_app.config.get("DOC_COMPILE_MEMORY_MB") or 0)
    except RuntimeError:
        configured = 0
    return configured or auto_memory_mb()


def _box_compile_lock():
    """Exclusive, box-wide lock file serialising tectonic across processes.

    The compile queue caps concurrency inside ONE process, but gunicorn runs
    several — so the queue alone still permits worker_count simultaneous
    compiles. On a small VPS that is an OOM. This lock makes "one tectonic at a
    time" true for the whole machine. Blocking: a caller would rather wait than
    fail, and the queue's own timeout still bounds the wait.
    """
    lock_path = _doc_render_root() / ".compile.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd


def _compile(tectonic: str, job_dir: Path, tex_path: Path,
             source_date_epoch: int, memory_mb: int = 0,
             should_abort=None, timeout: int = 0) -> bytes:
    """The ONE compile step: run tectonic on a prepared job dir and return the
    PDF bytes (or raise RenderError). Pure — no DB, no app context, no escaping;
    everything it needs is passed in, so it is safe to run on a worker thread.
    This is the single point every caller's compile funnels through.

    `should_abort` is an optional predicate checked once the box-wide lock is
    held: a caller that timed out while this job waited behind the lock has
    stopped reading the result, so there is no point spending the single
    machine-wide compile slot on it.

    `timeout` overrides `_COMPILE_TIMEOUT` for jobs that are legitimately
    slower than a one-page document (the abstract booklet, which is dozens of
    pages plus images)."""
    limit = timeout or _COMPILE_TIMEOUT
    # Network stays ON (a cold cache must still fetch packages).
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(source_date_epoch))
    lock_fd = _box_compile_lock()
    try:
        if should_abort is not None and should_abort():
            raise RenderError("compile abandoned before start (caller timed out)")
        proc = subprocess.run(
            [tectonic, "--outdir", str(job_dir), str(tex_path)],
            capture_output=True, timeout=limit,
            env=env, cwd=str(job_dir),
            preexec_fn=_memory_limiter(memory_mb),
        )
    except subprocess.TimeoutExpired as e:
        raise RenderError(
            f"tectonic timed out after {limit}s",
            log=_trim_log((e.stdout or b"") + (e.stderr or b"")))
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    if proc.returncode != 0:
        raise RenderError(
            f"tectonic exited {proc.returncode}",
            log=_trim_log(proc.stdout + proc.stderr))

    # tectonic names the output after the input stem, so this follows the
    # caller's .tex rather than assuming the document skeleton's name.
    pdf = job_dir / (tex_path.stem + ".pdf")
    if not pdf.exists():
        raise RenderError("tectonic produced no PDF",
                          log=_trim_log(proc.stdout + proc.stderr))
    return pdf.read_bytes()


class _CompileJob:
    """A prepared compile handed to the worker pool. The caller fills the job
    dir, enqueues, and waits on `done`; the worker runs `_compile` and stashes
    the bytes or the RenderError. `abandoned` lets a timed-out caller tell a
    not-yet-started job to skip the (now pointless) compile."""

    __slots__ = ("tectonic", "job_dir", "tex_path", "epoch", "memory_mb",
                 "timeout", "done", "pdf", "error", "abandoned")

    def __init__(self, tectonic: str, job_dir: Path, tex_path: Path,
                 epoch: int, memory_mb: int = 0, timeout: int = 0) -> None:
        self.tectonic = tectonic
        self.job_dir = job_dir
        self.tex_path = tex_path
        self.epoch = epoch
        self.memory_mb = memory_mb
        self.timeout = timeout
        self.done = threading.Event()
        self.pdf: bytes | None = None
        self.error: RenderError | None = None
        self.abandoned = False

    def execute(self) -> None:
        try:
            self.pdf = _compile(self.tectonic, self.job_dir, self.tex_path,
                                self.epoch, self.memory_mb,
                                should_abort=lambda: self.abandoned,
                                timeout=self.timeout)
        except RenderError as e:
            self.error = e
        except Exception as e:                # never let a worker die on a job
            self.error = RenderError(f"compile crashed: {e}")
        finally:
            self.done.set()


def _compile_worker() -> None:
    """Daemon worker: pull jobs forever, one compile at a time. Survives any
    single job's failure (execute() swallows everything) so the pool keeps
    serving after a bad template blows up a compile."""
    while True:
        job = _compile_queue.get()
        try:
            if job.abandoned:
                job.done.set()
                continue
            with _running_guard:
                global _running_jobs
                _running_jobs += 1
            try:
                job.execute()
            finally:
                with _running_guard:
                    _running_jobs -= 1
        finally:
            _compile_queue.task_done()


def _worker_count() -> int:
    try:
        n = int(current_app.config.get("DOC_COMPILE_WORKERS") or 1)
    except RuntimeError:
        n = 1
    return max(1, n)


def _ensure_workers() -> None:
    """Start the worker pool exactly once, on first submission (thread-safe
    double-checked start)."""
    global _workers_started
    if _workers_started:
        return
    with _workers_guard:
        if _workers_started:
            return
        for i in range(_worker_count()):
            threading.Thread(target=_compile_worker,
                             name=f"doc-compile-{i}", daemon=True).start()
        _workers_started = True


def _wait_timeout() -> int:
    try:
        return int(current_app.config.get("DOC_COMPILE_TIMEOUT")
                   or _QUEUE_WAIT_TIMEOUT)
    except RuntimeError:
        return _QUEUE_WAIT_TIMEOUT


def compile_backlog() -> int:
    """How many compile jobs are queued or actively running right now — i.e.
    the number a new submission would sit behind. Cheap; routes read it BEFORE
    kicking a render to report queue position (plan §6). 0 means "compiles
    immediately"."""
    return _compile_queue.qsize() + _running_jobs


def _submit_and_wait(job: _CompileJob, wait_timeout: int | None = None) -> bytes:
    """Enqueue a prepared job, block for its result up to the configured total
    wall-clock bound, and return the PDF bytes. On expiry the job is abandoned
    (skipped if still queued, its result discarded if mid-flight) and a
    RenderError is raised — the queue is left consistent.

    `wait_timeout` overrides the configured bound for jobs whose own compile is
    allowed to run longer than a document's (see `_compile`'s `timeout`)."""
    limit = wait_timeout or _wait_timeout()
    _ensure_workers()
    _compile_queue.put(job)
    if not job.done.wait(timeout=limit):
        job.abandoned = True
        raise RenderError(
            f"compile did not finish within {limit}s "
            f"(queue backlog too deep or tectonic stalled)")
    if job.error is not None:
        raise job.error
    return job.pdf if job.pdf is not None else b""


def compile_prepared_dir(job_dir: Path, tex_path: Path, *,
                         source_date_epoch: int = SOURCE_DATE_EPOCH,
                         compile_timeout: int = 0,
                         wait_timeout: int | None = None) -> bytes:
    """Compile an ALREADY-PREPARED job directory and return the PDF bytes.

    The public entry point for callers that build their own .tex tree rather
    than the document skeleton — currently the abstract booklet, whose LaTeX is
    generated per conference and `\\input`s one fragment per abstract. They get
    the same tectonic binary discovery, the same capped queue, the same box-wide
    lock and memory cap as `render_document`, so a booklet compile can't
    outrun the invoice renderer on a small VPS. Raises RenderError on failure.

    Must be called with an app context (config drives the memory cap); the
    compile itself then runs on a worker thread without one.
    """
    tectonic = _resolve_tectonic()
    return _submit_and_wait(
        _CompileJob(tectonic, job_dir, tex_path, source_date_epoch,
                    _compile_memory_mb(), compile_timeout),
        wait_timeout=wait_timeout)


def render_document(kind: str, vars_: dict[str, str],
                    assets: dict[str, Path] | None = None, *,
                    source_date_epoch: int = SOURCE_DATE_EPOCH,
                    template=None) -> bytes:
    """Compile a document to PDF bytes.

    Loads the DocumentTemplate for `kind` (or uses `template`, an unsaved draft
    with the DocumentTemplate fields, so the preview caller can render an
    uncommitted edit without a second code path), assembles the .tex from the
    shared skeleton (escaped structured content), copies any `assets` (`logo` /
    `signature` — referenced by the skeleton only when present), then compiles
    with SOURCE_DATE_EPOCH set for byte-reproducibility and returns the PDF
    bytes. The per-job directory is always removed in a finally.

    Two phases (plan §6): everything up to and including writing the .tex is the
    "prepare" step and runs here, in the caller's context (it touches the DB and
    escapes admin input). The tectonic run itself is handed to the process-wide
    compile queue and awaited, so bursts never exhaust the small VPS. The public
    signature and behaviour are unchanged — one code path, bytes out, RenderError
    on failure, job dir cleaned in finally.
    """
    from ..models import get_document_template

    # Default to the financial identity's letterhead/signature so every
    # caller — send, preview, regenerate — gets the same branding without
    # repeating the lookup. An explicit `assets` argument still wins.
    assets = assets if assets is not None else identity_assets()
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

        # Compile via the shared queue (capped concurrency) and wait for bytes.
        return _submit_and_wait(
            _CompileJob(tectonic, job, tex_path, source_date_epoch,
                        _compile_memory_mb()))
    finally:
        shutil.rmtree(job, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regeneration store (plan §12) — a CALLER of render_document. Rebuilds a stored
# IssuedDocument's PDF byte-identically from its snapshots. Lives here (not in
# the send layer) because it is pure rendering: stored vars + a template
# stand-in → the one renderer, with the pinned SOURCE_DATE_EPOCH. It touches no
# send/email concerns, so this is the cleaner home.
# ---------------------------------------------------------------------------

def regenerate_document(issued) -> bytes:
    """Rebuild the PDF for a stored `IssuedDocument` from its snapshots.

    Renders through the ONE renderer using the resolved variables captured at
    issue time and a template stand-in built from the stored render-affecting
    fields (so the result is faithful even if the live DocumentTemplate has been
    edited since). With the pinned SOURCE_DATE_EPOCH the bytes are identical to
    the originally issued document. Raises RenderError on a compile failure."""
    import json
    import types

    vars_ = json.loads(issued.vars_json or "{}")
    tpl_fields = json.loads(issued.template_json or "{}")
    # Documents issued before the tax treatment moved into the variables
    # recorded it template-side; honour that so older records still rebuild
    # with the GST lines they were issued with.
    if "gst_applies" not in vars_ and tpl_fields.get("gst_registered"):
        vars_["gst_applies"] = "1"
    template = types.SimpleNamespace(
        kind=issued.kind,
        pdf_body=tpl_fields.get("pdf_body", "") or "",
        content_hash=issued.content_hash or "",
    )
    return render_document(issued.kind, vars_, template=template)


def regenerate_cached(issued) -> bytes:
    """`regenerate_document` compiled once per stored document, then cached."""
    return cached_pdf(f"issued-{issued.id}-{issued.content_hash}",
                      lambda: regenerate_document(issued))


# ---------------------------------------------------------------------------
# Preview — a CALLER of render_document, never a second renderer (plan §5).
# ---------------------------------------------------------------------------

# The full variable vocabulary the skeleton consumes (every `esc()` text value
# plus invoice_type, which feeds the invoice title). Every preview variable
# lives here so an unfilled preview shows the field NAMES — including the
# numeric fields, which must never render as a computed $0.00.
_PREVIEW_VARS = (
    "site_name", "business_legal_name", "business_number", "business_address",
    "business_contact_email", "signatory_name", "signatory_role",
    "user_name", "user_email", "recipient_abn", "recipient_address",
    "conference_title", "conference_dates", "tier_name", "transaction_id",
    "registration_id", "payment_date", "due_date", "currency_symbol",
    "currency_code", "amount", "gst_amount", "amount_ex_gst",
    "payment_instructions", "payment_link", "invoice_type",
    "sanitized_invoice_ref", "payment_reference",
)


# Issuer facts: configured once in Financial Identity, identical on every
# document, and NOT something an admin fills in per document. A preview shows
# them for real — the same rule the letterhead images and the GST treatment
# already follow. Showing a bold `business_legal_name` in place of the name the
# admin just saved reads as "my settings didn't take".
_IDENTITY_VARS = (
    "business_legal_name", "business_number", "business_address",
    "business_contact_email", "payment_instructions",
    "signatory_name", "signatory_role",
)


def placeholder_vars(kind: str) -> dict[str, RawLatex]:
    """Full variable dict for a preview: per-document values become bold
    `\\textbf{name}` placeholders (the name itself LaTeX-escaped), while the
    configured issuer facts render for real.

    Every per-document variable is set to a truthy RawLatex, so the optional
    skeleton sections all appear showing their field names — and the numeric
    fields (amount / gst_amount / amount_ex_gst) read as their bold names,
    never a computed $0.00. An issuer field that is *unset* keeps its bold
    placeholder, so a preview still shows the admin what they have yet to fill
    in. Callers overlay real values on top."""
    from ..models import get_financial_identity

    vars_ = {name: RawLatex(r"\textbf{" + latex_escape(name) + "}")
             for name in _PREVIEW_VARS}

    ident = get_financial_identity()
    # GST is a configured fact, not a field to fill in: a preview shows the
    # tax treatment the identity is actually set to, so an admin can see
    # whether their documents will carry GST lines or the no-GST statement.
    registered = ident.gst_registered
    vars_["gst_applies"] = "1" if registered else ""
    vars_["gst_registered"] = "1" if registered else ""
    # A configured fact like the registration itself, so it shows for real
    # rather than as a placeholder — an admin proofing a 15% rate needs to see
    # 15 on the page.
    vars_["gst_rate"] = ident.gst_percent_label

    from ..models import get_site_settings
    real = {
        "business_legal_name": ident.legal_name or get_site_settings().site_name,
        "business_number": ident.abn,
        "business_address": ident.address,
        "business_contact_email": ident.contact_email,
        "payment_instructions": ident.payment_instructions,
        "signatory_name": ident.signatory_name,
        "signatory_role": ident.signatory_role,
    }
    for name in _IDENTITY_VARS:
        value = (real.get(name) or "").strip()
        if value:
            vars_[name] = value

    # Payment instructions are issuer free text that may itself contain
    # placeholders (typically "REF: {sanitized_invoice_ref}"). A real send
    # expands them before the value reaches the renderer, so a preview has to
    # as well or the admin sees the raw braces and cannot tell whether they
    # wrote the name correctly.
    #
    # Substituted with the plain field NAME, not the bold RawLatex the other
    # placeholders use: this value goes on to be escaped as a leaf, so any
    # markup folded in here would be printed literally — the same trap that
    # once surfaced \TEXTBF{INVOICE\_TYPE} on the page.
    text = vars_.get("payment_instructions")
    if text and not isinstance(text, RawLatex):
        for name in _PREVIEW_VARS:
            text = text.replace("{" + name + "}", name)
        vars_["payment_instructions"] = text
    return vars_


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


def _issued_cache_dir() -> Path:
    return _doc_render_root() / "issued"


def cached_pdf(key: str, build) -> bytes:
    """The PDF for *key*, compiling it only the first time it is asked for.

    Safe because a document's bytes are a pure function of its inputs: the
    pinned SOURCE_DATE_EPOCH and the snapshotted variables and template make
    every rebuild identical, so a hit can never be stale. Callers put the
    template's content hash in the key, so editing a template misses rather
    than serving the old design.

    Purely a cache. The directory can be deleted at any time.
    """
    dest = _issued_cache_dir() / f"{key}.pdf"
    try:
        return dest.read_bytes()
    except OSError:
        pass

    pdf = build()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp = tempfile.mkstemp(prefix=".doc-", suffix=".tmp",
                                       dir=str(dest.parent))
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(pdf)
            os.replace(tmp, dest)       # Atomic: readers see whole files only.
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        log.warning("Could not cache document %s", key, exc_info=True)
    return pdf


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


def _xproc_warm_lock(kind: str) -> int | None:
    """Cross-process lock so only one gunicorn worker compiles `kind` at a time.

    Returns an open fd on success (caller MUST call `_xproc_warm_unlock`).
    Returns None if another process already holds the lock.
    """
    d = _pregen_dir()
    d.mkdir(parents=True, exist_ok=True)
    lock_path = d / f".warm-{kind}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, BlockingIOError):
        try:
            os.close(fd)
        except Exception:
            pass
        return None


def _xproc_warm_unlock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass


def warm_pregen(kind: str) -> Path:
    """Compile the all-placeholders preview for the SAVED template and cache it
    atomically at `var/doc-render/pregen/<kind>-<content_hash>.pdf`, removing any
    stale pregen files for that kind. Held under a cross-process lock AND a
    per-kind intra-process lock; if a warm for this kind is already running
    (anywhere in the process, or in another gunicorn worker), raises PregenBusy
    rather than compiling twice.
    """
    from ..models import get_document_template

    content_hash = get_document_template(kind).content_hash
    dest = _pregen_path(kind, content_hash)
    if dest.exists():
        return dest

    xlock = _xproc_warm_lock(kind)
    if xlock is None:
        raise PregenBusy(kind)

    try:
        lock = _pregen_lock(kind)
        if not lock.acquire(blocking=False):
            raise PregenBusy(kind)
        try:
            if dest.exists():
                return dest
            pdf = preview_document(kind)  # saved template, all placeholders
            d = _pregen_dir()
            d.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp = tempfile.mkstemp(
                prefix=f".{kind}-", suffix=".tmp", dir=str(d),
            )
            try:
                with os.fdopen(tmp_fd, "wb") as fh:
                    fh.write(pdf)
                os.replace(tmp, dest)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            for old in d.glob(f"{kind}-*.pdf"):
                if old != dest:
                    old.unlink(missing_ok=True)
            return dest
        finally:
            lock.release()
    finally:
        _xproc_warm_unlock(xlock)


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
    The compile itself rides the shared queue (warm_pregen → preview_document →
    render_document → queue), so a boot/save warm never contends with an
    on-demand render for CPU; this daemon thread only owns the per-kind pregen
    lock while it waits for its queued compile."""
    if app.config.get("TESTING"):
        return
    from ..models import get_document_template
    if _pregen_path(kind, get_document_template(kind).content_hash).exists():
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
