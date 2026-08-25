"""Work that must not be done while a request is open.

Rendering a PDF takes seconds and is serialised box-wide, and sending mail
holds a process-wide lock. Doing either inside a request means the worker
handling it is unavailable for that whole time — so a rush of submissions on
the night of a deadline does not queue politely, it takes the site down for
everyone, including people only trying to read a page.

Notifications go through here instead. The member is told their submission
landed as soon as it is saved, and the confirmation follows a moment later.

One worker thread per process, because the compiles these jobs perform are
serialised across the whole box anyway — more threads would only queue against
that lock while holding database connections.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger(__name__)

# Deep enough for any plausible burst — a deadline evening is hundreds of
# submissions at worst, and each job is seconds. Bounded so a stuck worker
# cannot exhaust memory.
_MAX_PENDING = 2000

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=_MAX_PENDING)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def pending() -> int:
    """How many jobs are waiting. For diagnostics and tests."""
    return _queue.qsize()


def _worker_name() -> str:
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}"


def _run() -> None:
    from ..extensions import db
    from ..models.queue_stat import record_depth

    name = _worker_name()
    while True:
        app, fn, base_url = _queue.get()
        try:
            # A request context, not merely an application one: these jobs
            # send mail carrying links, and building an external URL outside a
            # request needs SERVER_NAME, which a site does not otherwise need
            # to set. The scheduling request knew its own address, so the job
            # borrows it and the links come out on the host the visitor used.
            ctx = (app.test_request_context(base_url=base_url) if base_url
                   else app.app_context())
            with ctx:
                # Before and after: a burst is visible while it is being
                # worked through, not only once it has drained.
                record_depth(name, _queue.qsize() + 1)
                fn()
        except Exception:
            log.exception("Background job failed")
        finally:
            try:
                with app.app_context():
                    record_depth(name, _queue.qsize())
            except Exception:
                pass
            try:
                # The session is thread-local; leaving it open would hold a
                # pooled connection for the life of the process.
                db.session.remove()
            except Exception:
                pass
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="tasks",
                                       daemon=True)
            _worker.start()


def run_later(fn) -> bool:
    """Run *fn* — taking no arguments — after this request has been answered.

    Runs inline under TESTING so tests stay deterministic, and inline when
    there is no application to carry into the thread. Returns whether it was
    deferred.
    """
    from flask import current_app, has_app_context, has_request_context, request

    if not has_app_context():
        fn()
        return False
    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        fn()
        return False

    # Carried into the worker so the job can build links on the same host the
    # visitor reached us on. Without it, anything emailing a URL fails.
    base_url = request.url_root if has_request_context() else None

    _ensure_worker()
    try:
        _queue.put_nowait((app, fn, base_url))
        return True
    except queue.Full:
        # Running it here would block the request, which is the thing this
        # exists to prevent; the backlog is already the emergency.
        log.error("Background queue full (%d) — dropped a job", _MAX_PENDING)
        return False


def run_later_for(model, obj_id, fn, **kwargs) -> bool:
    """Run `fn(instance, **kwargs)` in the background for a model row.

    The instance is re-read inside the job: the one the request was holding
    belongs to that request's session, which is gone by then.
    """
    def _job():
        from ..extensions import db

        obj = db.session.get(model, obj_id)
        if obj is None:
            log.warning("%s %s no longer exists; skipping %s",
                        model.__name__, obj_id, getattr(fn, "__name__", fn))
            return
        fn(obj, **kwargs)

    return run_later(_job)
