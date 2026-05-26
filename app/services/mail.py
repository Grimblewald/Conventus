"""Pluggable mail backend. console | smtp.

Reads `MAIL_BACKEND` and SMTP settings from environment at send time so
admin can rotate SMTP credentials without restarting the app (env reload
not implemented yet — but config-not-read-at-import-time keeps that future
option open).
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


log = logging.getLogger(__name__)


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
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587").strip() or 587)
    user = (os.environ.get("SMTP_USER") or "").strip()
    pw = (os.environ.get("SMTP_PASS") or "").strip()
    timeout = int(os.environ.get("SMTP_TIMEOUT", "15").strip() or 15)
    sender = os.environ.get("MAIL_FROM", "noreply@example.org").strip()

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)

    # 465 = implicit TLS, 587 = STARTTLS, 25 = plaintext (avoid).
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
            if user:
                s.login(user, pw)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.ehlo()
            if port != 25:
                s.starttls()
                s.ehlo()
            if user:
                s.login(user, pw)
            s.send_message(msg)
