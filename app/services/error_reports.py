"""Telling the administrator that something broke.

The 500 page has always said "the site administrator has been notified". That
was untrue: the handler rendered a template and nothing else, so every crash a
member hit was invisible unless someone happened to be reading the process
output. This module makes the sentence true.

Server logs go to stdout under gunicorn, so there is no file to reach back
into after the fact. Instead a ring buffer keeps the most recent records in
memory, and a report carries the slice around the failure — which is what
somebody reading the email actually needs, along with the traceback and enough
about the request to reproduce it.
"""
from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# Enough to cover the request that failed and a little of what led to it.
_BUFFER_SIZE = 300

# One report per distinct failure per interval. A crash in a hot path would
# otherwise send a message per request and bury the inbox exactly when it is
# most needed.
_MIN_INTERVAL = timedelta(minutes=15)

_buffer: deque[str] = deque(maxlen=_BUFFER_SIZE)
_buffer_lock = threading.Lock()
_last_sent: dict[str, datetime] = {}
_sent_lock = threading.Lock()


class RingBufferHandler(logging.Handler):
    """Keeps the most recent log lines in memory for crash reports."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with _buffer_lock:
            _buffer.append(line)


def install(app) -> None:
    """Attach the ring buffer to the root logger."""
    handler = RingBufferHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    app.extensions["error_report_buffer"] = handler


def recent_log_lines(limit: int = 60) -> list[str]:
    with _buffer_lock:
        return list(_buffer)[-limit:]


def _signature(exc: BaseException) -> str:
    """Identifies a failure for rate-limiting: type plus where it was raised."""
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    where = (f"{last.tb_frame.f_code.co_filename}:{last.tb_lineno}"
             if last else "?")
    return f"{type(exc).__name__}@{where}"


def _should_send(signature: str) -> bool:
    now = datetime.utcnow()
    with _sent_lock:
        previous = _last_sent.get(signature)
        if previous is not None and now - previous < _MIN_INTERVAL:
            return False
        _last_sent[signature] = now
        return True


def report_exception(exc: BaseException) -> bool:
    """Email the site's admins about an unhandled exception.

    Returns whether a message was actually sent, so the error page can say
    "the administrator has been notified" only when that is true. Never
    raises: a failure in here must not replace the error the user already
    has with a second one.
    """
    try:
        from flask import current_app, has_request_context, request
        from flask_login import current_user

        from ..models.content import get_site_settings
        from ..models.user import User
        from .mail import send_mail

        signature = _signature(exc)
        if not _should_send(signature):
            log.warning("Suppressed duplicate error report for %s", signature)
            return False

        admins = User.query.filter(User.role_name == "admin",
                                   User.deleted_at.is_(None)).all()
        recipients = [u.email for u in admins if u.email]
        if not recipients:
            log.error("Unhandled exception and no admin to notify: %s", signature)
            return False

        when = datetime.utcnow()
        lines = [
            f"An unhandled error occurred on {get_site_settings().site_name}.",
            "",
            f"Time (UTC):  {when:%Y-%m-%d %H:%M:%S}",
            f"Error:       {type(exc).__name__}: {exc}",
        ]
        if has_request_context():
            who = "anonymous"
            try:
                if current_user.is_authenticated:
                    who = current_user.email
            except Exception:
                pass
            lines += [
                f"Request:     {request.method} {request.path}",
                f"Endpoint:    {request.endpoint}",
                f"User:        {who}",
                f"Client:      {request.remote_addr}",
                f"User agent:  {request.headers.get('User-Agent', '')[:200]}",
            ]
        lines += ["", "Traceback", "---------",
                  "".join(traceback.format_exception(
                      type(exc), exc, exc.__traceback__))[-6000:]]

        recent = recent_log_lines()
        if recent:
            lines += ["", f"Server log — last {len(recent)} lines", "-" * 40]
            lines += recent
        lines += ["",
                  f"Further reports of this same failure are suppressed for "
                  f"{int(_MIN_INTERVAL.total_seconds() // 60)} minutes."]

        ok = send_mail(
            to=recipients[0],
            cc=recipients[1:] or None,
            subject=f"[{get_site_settings().site_name}] Error: "
                    f"{type(exc).__name__} at {request.path if has_request_context() else 'n/a'}",
            body="\n".join(lines),
        )
        if not ok:
            log.error("Could not email the error report for %s", signature)
        return bool(ok)
    except Exception:
        log.exception("Error reporting itself failed")
        return False
