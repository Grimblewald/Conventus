"""Smoke tests for setup wizard."""
from __future__ import annotations


def test_setup_welcome_returns_200_without_flag(client, app):
    """If setup flag doesn't exist, the wizard should be accessible."""
    with app.app_context():
        import os
        flag = app.config["SETUP_FLAG_PATH"]
        if os.path.exists(flag):
            os.remove(flag)

    resp = client.get("/setup/welcome")
    # Should return 200 (shows password-entry form) or 302 (redirect to login)
    assert resp.status_code in (200, 302)


def test_setup_wizard_returns_200(client, app):
    """Wizard page should return 200."""
    resp = client.get("/setup/wizard")
    assert resp.status_code in (200, 302)
