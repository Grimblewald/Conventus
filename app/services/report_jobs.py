"""The registration report PDF, made in the background with visible progress.

Compiling the PDF takes seconds, more when the box-wide compile queue is busy
with invoices. Done inside the request, the page just hangs, and the people
pressing the button press it again. So the button starts a job here and
returns at once; the page polls the job, shows each step as it happens, and
downloads the file when it is ready.

The site runs several gunicorn workers, and a poll can reach any of them, so
a job's state cannot live in memory: each job is a directory under
instance/report-jobs/ holding status.json and, once made, the PDF. instance/
is never web-served. Jobs older than an hour are swept away whenever a new
one starts.

Pressing again while a job runs — a second tab, a double click the page did
not catch — returns the job already running for the same person, conference
and columns rather than starting another.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import threading
import time
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

# The steps the page lists, in order. "queued" is shown only when the compile
# queue has something ahead of this job.
STEPS = (
    ("count", "Counting registrations"),
    ("chart", "Drawing the chart"),
    ("queued", "Waiting for the PDF typesetter"),
    ("typeset", "Typesetting the PDF"),
    ("ready", "Ready"),
)
STEP_LABELS = dict(STEPS)

_KEEP_SECONDS = 3600
# A job that has not moved for this long was lost with the worker running it
# (a restart, say); the page is told so rather than waiting forever.
_STALE_SECONDS = 600
_JOB_ID_LEN = 22
# A wait for the typesetter shorter than this is not worth listing.
_SHOW_WAIT_AFTER = 1.0
_lock = threading.Lock()


def _root() -> Path:
    return Path(current_app.instance_path) / "report-jobs"


def _dir(job_id: str) -> Path | None:
    """The job's directory, or None for anything that is not a job id we
    could have minted — the id arrives in a URL."""
    if not job_id or len(job_id) > 64 or not all(
            ch.isalnum() or ch in "-_" for ch in job_id):
        return None
    return _root() / job_id


def _write(path: Path, status: dict) -> None:
    """Replace status.json in one step, so a poll never reads half of it."""
    tmp = path / f".status.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(status))
    os.replace(tmp, path / "status.json")


def read(job_id: str) -> dict | None:
    d = _dir(job_id)
    if d is None:
        return None
    try:
        status = json.loads((d / "status.json").read_text())
    except (OSError, ValueError):
        return None
    if (status.get("state") == "running"
            and time.time() - status.get("updated", 0) > _STALE_SECONDS):
        status.update(state="error",
                      error="The PDF stopped being made part-way (the site may "
                            "have restarted). Please press Download PDF again.")
    return status


def pdf_path(job_id: str) -> Path | None:
    d = _dir(job_id)
    return d / "report.pdf" if d is not None else None


def _sweep() -> None:
    root = _root()
    if not root.exists():
        return
    cutoff = time.time() - _KEEP_SECONDS
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _running_for(user_id: int, conference_id: int, stages: list[str]) -> str | None:
    root = _root()
    if not root.exists():
        return None
    for d in root.iterdir():
        status = read(d.name)
        if (status and status.get("state") == "running"
                and status.get("user_id") == user_id
                and status.get("conference_id") == conference_id
                and status.get("stages") == stages):
            return d.name
    return None


def start(conference, stages: list[str], user_id: int) -> tuple[str, bool]:
    """Start making the PDF, or find the job already making it.

    Returns (job id, started) — started is False when an identical job was
    already running and its id is handed back instead.
    """
    with _lock:
        _sweep()
        existing = _running_for(user_id, conference.id, stages)
        if existing:
            return existing, False
        job_id = secrets.token_urlsafe(16)[:_JOB_ID_LEN]
        d = _root() / job_id
        d.mkdir(parents=True)
        now = time.time()
        _write(d, {"state": "running", "step": "count", "done": [],
                   "user_id": user_id, "conference_id": conference.id,
                   "stages": stages, "started": now, "updated": now,
                   "filename": None, "error": None})

    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        _run(app, job_id, conference.id, stages)
    else:
        threading.Thread(target=_run, args=(app, job_id, conference.id, stages),
                         name=f"report-{job_id[:6]}", daemon=True).start()
    return job_id, True


def _run(app, job_id: str, conference_id: int, stages: list[str]) -> None:
    from ..extensions import db
    from ..models import Conference
    from ..models.content import get_site_settings
    from . import documents
    from . import registration_report as rr

    d = _root_for(app) / job_id
    status = json.loads((d / "status.json").read_text())

    def step(name: str) -> None:
        if status["step"] and status["step"] != name:
            status["done"].append(status["step"])
        status.update(step=name, updated=time.time())
        _write(d, status)

    try:
        with app.app_context():
            conference = db.session.get(Conference, conference_id)
            report = rr.build(conference)
            step("chart")
            sankey = rr.build_sankey(conference, stages, width=480, height=330)
            # Waiting until the compile itself says it has begun: the wait can
            # be in this process's queue or behind another worker's compile,
            # and only the compile knows when it is over.
            status["queued_at"] = time.time()
            step("queued")

            def started() -> None:
                status["waited"] = time.time() - status["queued_at"]
                step("typeset")
            pdf = rr.render_pdf(report, sankey,
                                currency_symbol=get_site_settings().currency_symbol or "$",
                                on_start=started)
            if status["step"] != "typeset":
                step("typeset")      # a compile that never reported its start
            (d / "report.pdf").write_bytes(pdf)
            status["filename"] = rr.pdf_filename(conference, report)
            step("ready")
            status.update(state="done", done=status["done"] + ["ready"])
            _write(d, status)
    except Exception as exc:     # shown on the page; logged in full here
        log.exception("Registration report PDF failed (job %s)", job_id)
        message = str(exc) if isinstance(exc, documents.RenderError) else (
            "Something went wrong while making the PDF.")
        status.update(state="error", error=message, updated=time.time())
        _write(d, status)
    finally:
        try:
            with app.app_context():
                db.session.remove()
        except Exception:
            pass


def _root_for(app) -> Path:
    return Path(app.instance_path) / "report-jobs"


def public(status: dict) -> dict:
    """What the page is told: the steps, each done / current / to come, and
    the outcome. Nothing about who asked."""
    current = status.get("step")
    done = set(status.get("done") or [])
    now = time.time()
    # The wait for the typesetter is shown only when there was one worth
    # mentioning: under a second it is just the hand-over, and listing it
    # would tell people they waited when they did not.
    waited = (status.get("waited") if "queued" in done
              else now - status.get("queued_at", now) if current == "queued" else 0)
    show_queue = (waited or 0) >= _SHOW_WAIT_AFTER
    steps = []
    for key, label in STEPS:
        if key == "queued" and not show_queue:
            continue
        state = ("done" if key in done
                 else "current" if key == current and status["state"] == "running"
                 else "todo")
        steps.append({"key": key, "label": label, "state": state})
    # While the queue wait is hidden, the page shows typesetting as under way.
    if current == "queued" and not show_queue and status["state"] == "running":
        for st in steps:
            if st["key"] == "typeset":
                st["state"] = "current"
    return {"state": status["state"], "steps": steps,
            "elapsed": round(now - status.get("started", now), 1),
            "error": status.get("error"), "filename": status.get("filename")}
