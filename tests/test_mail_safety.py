"""The suite can never email anyone.

conftest forces the console backend and refuses every SMTP connection. These
check both, so a later change to either shows up as a failure here rather than
as mail in a stranger's inbox.
"""
from __future__ import annotations

import os


def test_the_backend_is_console():
    assert os.environ["MAIL_BACKEND"] == "console"


def test_an_smtp_send_cannot_connect(app, monkeypatch):
    from app.services import mail

    monkeypatch.setenv("MAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setattr(mail, "_smtp_conn", None)
    with app.app_context():
        assert mail.send_mail(to="someone@example.org", subject="x", body="x") is False
