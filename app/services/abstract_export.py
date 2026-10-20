"""The admin abstracts-panel export: a LaTeX bundle built off the request and
handed over by an emailed link that only its requester can use.

Building a bundle reads and transcodes every figure and looks up each
reference's citation, which for a large selection takes minutes. A request
that did that held a worker the whole time, so the request now only records
who asked; the task worker builds the bundle and emails the link.

Kept under <uploads>/abstracts/.export-cache:
  <key>.zip           a bundle, keyed on everything that went into it, so an
                      unchanged selection is never built twice
  <key>.building      held while a build runs, so concurrent requests for the
                      same bundle share one build
  grant-<token>.json  one per request: who may fetch which bundle, until when,
                      and whether they have been emailed
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app, url_for

log = logging.getLogger(__name__)

GRANT_TTL = timedelta(hours=48)
# A build marker older than this belongs to a process that died mid-build.
BUILD_STALE = timedelta(minutes=30)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def cache_dir() -> Path:
    d = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts" / ".export-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_key(abstracts) -> str:
    """Identifies a bundle by its contents: the abstracts as last saved, and
    the conferences whose title and dates head each booklet file."""
    parts = sorted(f"{a.id}:{a.status}:{a.updated_at.isoformat()}"
                   for a in abstracts)
    confs = {a.conference for a in abstracts if a.conference is not None}
    parts += sorted(f"c{c.id}:{c.updated_at.isoformat()}" for c in confs)
    return hashlib.sha256(",".join(parts).encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def write_bundle(abstracts, dest: Path) -> None:
    """Write the LaTeX bundle for *abstracts* to the zip at *dest*.

    One folder per abstract, named by its reference, holding a document that
    compiles as-is inside that folder, the fragment it inputs, and the
    converted images. At the root, one booklet .tex per conference in the
    selection pulls in the same fragments, plus a manifest.

    Unlike the booklet export, figures are set against the full page and an
    abstract may run over pages, so a large figure is never squashed into the
    space left on one. Conference decoration stays out: it belongs to the
    booklet design panel.
    """
    from ..models.abstract import status_label
    from .abstract_latex import (abstract_fragment, booklet_preamble,
                                 copy_abstract_images, standalone_preamble)

    uploads_root = Path(current_app.config["UPLOAD_FOLDER"])

    # Programme order inside each conference (oldest first, as the booklet
    # does); conferences in the order the panel's filter dropdown lists them.
    confs = sorted({a.conference for a in abstracts if a.conference is not None},
                   key=lambda c: c.start_date, reverse=True)
    ordered = [(c, a) for c in confs
               for a in sorted((a for a in abstracts if a.conference_id == c.id),
                               key=lambda a: a.created_at)]
    inputs_by_conf: dict[int, list[str]] = {c.id: [] for c in confs}

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp)
        for c, a in ordered:
            folder = a.reference
            sub = src / folder
            sub.mkdir(parents=True, exist_ok=True)
            # Figure paths relative to the folder, so the document inside it
            # compiles where it stands; the booklet reaches the same fragment
            # through \subimport, which resolves them against the folder too.
            (sub / "abstract.tex").write_text(
                abstract_fragment(folder, a, fit_single_page=False,
                                  figure_dir=""),
                encoding="utf-8")
            (sub / f"{folder}.tex").write_text(
                standalone_preamble("\\input{abstract.tex}"),
                encoding="utf-8")
            inputs_by_conf[c.id].append(
                f"\\subimport{{{folder}/}}{{abstract.tex}}")
            copy_abstract_images(a, uploads_root=uploads_root, dest_dir=sub)

        for c in confs:
            (src / f"booklet-{c.slug}.tex").write_text(
                booklet_preamble(c, inputs_by_conf[c.id], None, None, None,
                                 packages=("import",)),
                encoding="utf-8")

        with (src / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["reference", "id", "conference", "status", "title",
                        "authors", "track", "tex"])
            for c, a in ordered:
                w.writerow([a.reference, a.id, c.slug, status_label(a.status),
                            a.title, a.authors, a.track or "",
                            f"{a.reference}/{a.reference}.tex"])

        # Written aside and moved into place, so a reader never sees half a zip.
        part = dest.with_suffix(".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(src).as_posix())
        os.replace(part, dest)


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------

def request_export(user, abstracts, *, download_name: str,
                   description: str) -> None:
    """Record that *user* wants the bundle for *abstracts*, and see that it is
    built and they are emailed a link to it.

    A bundle already built for the same contents is reused. One already being
    built is joined: that build emails everyone waiting on it.
    """
    from .tasks import run_later

    _purge_expired()
    key = bundle_key(abstracts)
    token = secrets.token_urlsafe(24)
    _write_grant(token, {
        "user_id": user.id,
        "key": key,
        "count": len(abstracts),
        "description": description,
        "download_name": download_name,
        "created_at": datetime.utcnow().isoformat(),
        "notified": False,
    })

    if (cache_dir() / f"{key}.zip").exists():
        run_later(lambda: _notify(key, ok=True))
    elif _claim_build(key):
        ids = [a.id for a in abstracts]
        run_later(lambda: _build_and_notify(key, ids))


def _build_and_notify(key: str, ids: list[int]) -> None:
    from ..models import Abstract

    d = cache_dir()
    try:
        abstracts = Abstract.query.filter(Abstract.id.in_(ids)).all()
        write_bundle(abstracts, d / f"{key}.zip")
        ok = True
    except Exception:
        log.exception("Abstract export %s failed", key)
        ok = False
    finally:
        (d / f"{key}.building").unlink(missing_ok=True)
    _notify(key, ok=ok)


def _claim_build(key: str) -> bool:
    """Take the build for *key*, unless a live build already holds it."""
    marker = cache_dir() / f"{key}.building"
    for _ in range(2):
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = datetime.now() - datetime.fromtimestamp(marker.stat().st_mtime)
            if age < BUILD_STALE:
                return False
            marker.unlink(missing_ok=True)
            continue
        os.close(fd)
        return True
    return False


def _notify(key: str, *, ok: bool) -> None:
    """Email everyone waiting on bundle *key*, once each."""
    from ..extensions import db
    from ..models import User
    from .mail import send_mail

    for path in cache_dir().glob("grant-*.json"):
        grant = _read_json(path)
        if not grant or grant.get("key") != key or grant.get("notified"):
            continue
        user = db.session.get(User, grant["user_id"])
        if user is None or not user.email:
            continue
        token = path.stem.removeprefix("grant-")
        n = grant["count"]
        what = f"{n} abstract{'s' if n != 1 else ''} ({grant['description']})"
        if ok:
            expires = (datetime.fromisoformat(grant["created_at"])
                       + GRANT_TTL).strftime("%-d %B %Y %H:%M UTC")
            link = url_for("admin.abstract_export_download", token=token,
                           _external=True)
            subject = "Abstract export ready"
            body = (f"Your export of {what} is ready:\n\n{link}\n\n"
                    f"This link works only for you, while signed in to your "
                    f"account, until {expires}. Anyone else who needs this "
                    f"export should request their own from Admin → Abstracts.")
        else:
            subject = "Abstract export failed"
            body = (f"Your export of {what} could not be built. The error has "
                    f"been logged. Please try again from Admin → Abstracts, and "
                    f"tell the site administrator if it keeps failing.")
        if send_mail(to=user.email, subject=subject, body=body):
            grant["notified"] = True
            _write_grant(token, grant)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def open_grant(token: str, user_id: int) -> tuple[str, dict | None, Path | None]:
    """Resolve an emailed link for the signed-in user.

    Returns (state, grant, zip path). State is "ok", "missing" (no such link,
    or not this user's — the two are indistinguishable on purpose), "expired",
    or "gone" (the bundle has been cleared away).
    """
    if not _TOKEN_RE.match(token or ""):
        return "missing", None, None
    grant = _read_json(cache_dir() / f"grant-{token}.json")
    if not grant or grant.get("user_id") != user_id:
        return "missing", None, None
    if datetime.utcnow() - datetime.fromisoformat(grant["created_at"]) > GRANT_TTL:
        return "expired", grant, None
    path = cache_dir() / f"{grant['key']}.zip"
    if not path.exists():
        return "gone", grant, None
    return "ok", grant, path


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

def _write_grant(token: str, grant: dict) -> None:
    path = cache_dir() / f"grant-{token}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(grant), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _purge_expired() -> None:
    """Drop expired grants, and bundles no live grant still points at."""
    d = cache_dir()
    now = datetime.utcnow()
    live_keys: set[str] = set()
    for path in d.glob("grant-*.json"):
        grant = _read_json(path)
        if (not grant or now - datetime.fromisoformat(grant["created_at"])
                > GRANT_TTL):
            path.unlink(missing_ok=True)
        else:
            live_keys.add(grant["key"])
    for path in d.glob("*.zip"):
        if path.stem not in live_keys:
            path.unlink(missing_ok=True)
