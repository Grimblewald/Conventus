"""Pluggable mail backend. console | smtp.

Reads `MAIL_BACKEND` and SMTP settings from environment at send time so
admin can rotate SMTP credentials without restarting the app (env reload
not implemented yet — but config-not-read-at-import-time keeps that future
option open).

SMTP connections are persistent at the process level — a single connection
is established on first use (or eagerly at app startup) and reused across
all requests. A module-level lock ensures thread safety.
"""
from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


log = logging.getLogger(__name__)

_smtp_conn: smtplib.SMTP | None = None
_smtp_lock = threading.Lock()


def connect_mailer() -> None:
    """Eagerly establish the SMTP connection at startup (no-op if console)."""
    backend = os.environ.get("MAIL_BACKEND", "console").strip().lower()
    if backend != "smtp":
        return
    try:
        _get_smtp_connection()
        log.info("SMTP connection established")
    except Exception:
        log.warning("SMTP connect failed — will retry on first send_mail()",
                    exc_info=True)


def send_mail(to: str, subject: str, body: str) -> bool:
    """Returns True on success, False on failure. Never raises."""
    backend = os.environ.get("MAIL_BACKEND", "console").strip().lower()
    try:
        if backend == "smtp":
            _send_smtp(to, subject, body)
        else:
            _send_console(to, subject, body)
        return True
    except Exception:
        log.exception("send_mail(%r) failed", to)
        return False


def _send_console(to: str, subject: str, body: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n[mail:console] to={to}\n[mail:console] subject={subject}\n"
          f"{'-' * 72}\n{body}\n{bar}\n", flush=True)


def _send_smtp(to: str, subject: str, body: str) -> None:
    from flask import current_app
    sender = current_app.config.get("MAIL_FROM", "").strip() or "noreply@example.org"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)

    _get_smtp_connection().send_message(msg)


def _get_smtp_connection() -> smtplib.SMTP:
    """Return a connected, authenticated SMTP object.  Thread-safe."""
    global _smtp_conn
    with _smtp_lock:
        if _smtp_conn is not None:
            try:
                _smtp_conn.noop()
            except Exception:
                log.debug("SMTP connection dropped — reconnecting")
                try:
                    _smtp_conn.quit()
                except Exception:
                    pass
                _smtp_conn = None
        if _smtp_conn is None:
            _smtp_conn = _connect_smtp()
    return _smtp_conn


def _connect_smtp() -> smtplib.SMTP:
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587").strip() or 587)
    user = (os.environ.get("SMTP_USER") or "").strip()
    pw = (os.environ.get("SMTP_PASS") or "").strip()
    timeout = int(os.environ.get("SMTP_TIMEOUT", "15").strip() or 15)

    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        s = smtplib.SMTP(host, port, timeout=timeout)
        s.ehlo()
        if port != 25:
            s.starttls()
            s.ehlo()
    if user:
        s.login(user, pw)
    return s
