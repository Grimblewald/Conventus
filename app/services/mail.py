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
from email.utils import formataddr, formatdate, make_msgid, parseaddr


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


def send_mail(to: str, subject: str, body: str, sender_name: str | None = None,
              reply_to: str | None = None, sender_email: str | None = None,
              attachments: list[tuple[str, bytes, str]] | None = None,
              cc: list[str] | None = None) -> bool:
    """Returns True on success, False on failure. Never raises.

    If *sender_name* is given, it replaces the display-name portion of
    MAIL_FROM (e.g. "Contact Form" <noreply@example.org>). If
    *sender_email* is given, it replaces the address portion. Emails are
    always plaintext; *attachments* is a list of (filename, content,
    mimetype) tuples attached to the message. *cc* addresses receive a
    copy and appear in the Cc header.
    """
    backend = os.environ.get("MAIL_BACKEND", "console").strip().lower()
    try:
        if backend == "smtp":
            _send_smtp(to, subject, body, sender_name, reply_to, sender_email, attachments, cc)
        else:
            _send_console(to, subject, body, sender_name, reply_to, sender_email, attachments, cc)
        return True
    except Exception:
        log.exception("send_mail(%r) failed", to)
        return False


def _send_console(to: str, subject: str, body: str,
                  sender_name: str | None = None,
                  reply_to: str | None = None,
                  sender_email: str | None = None,
                  attachments: list[tuple[str, bytes, str]] | None = None,
                  cc: list[str] | None = None) -> None:
    bar = "=" * 72
    from_bits = " ".join(filter(None, [sender_name, sender_email]))
    from_label = f" (from: {from_bits})" if from_bits else ""
    reply_label = f" (reply-to: {reply_to})" if reply_to else ""
    cc_label = f" (cc: {', '.join(cc)})" if cc else ""
    attach_label = f" (+{len(attachments)} attachment(s))" if attachments else ""
    print(f"\n{bar}\n[mail:console] to={to}{cc_label}{from_label}{reply_label}{attach_label}\n[mail:console] subject={subject}\n"
          f"{'-' * 72}\n{body}\n{bar}\n", flush=True)


def _send_smtp(to: str, subject: str, body: str,
               sender_name: str | None = None,
               reply_to: str | None = None,
               sender_email: str | None = None,
               attachments: list[tuple[str, bytes, str]] | None = None,
               cc: list[str] | None = None) -> None:
    from flask import current_app
    raw_from = current_app.config.get("MAIL_FROM", "").strip() or "noreply@example.org"
    if sender_name or sender_email:
        display, addr = parseaddr(raw_from)
        addr = sender_email or addr
        sender = formataddr((sender_name or display, addr)) if addr else raw_from
    else:
        sender = raw_from

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    # Extract domain from sender address for a valid Message-ID domain
    # (avoid socket.getfqdn() leaking internal hostnames like .localdomain)
    _d, addr = parseaddr(sender)
    mid_domain = addr.split("@")[-1] if "@" in addr else "conventus.local"
    msg["Message-ID"] = make_msgid(domain=mid_domain)
    msg["Date"] = formatdate(localtime=True)
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    for filename, content, mimetype in attachments or []:
        maintype, subtype = mimetype.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    conn = _get_smtp_connection()
    try:
        conn.send_message(msg)
    except smtplib.SMTPException:
        _reset_smtp_connection()
        _get_smtp_connection().send_message(msg)


def _reset_smtp_connection() -> None:
    global _smtp_conn
    with _smtp_lock:
        if _smtp_conn is not None:
            try:
                _smtp_conn.quit()
            except Exception:
                pass
            _smtp_conn = None


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
