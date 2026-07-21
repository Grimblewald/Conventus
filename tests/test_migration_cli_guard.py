"""Tests for the `flask db` CLI detection that gates db.create_all().

The app factory must skip schema bootstrap when the Flask-Migrate CLI is
driving, so migrations observe the true pre-migration schema instead of a
set of empty tables pre-created from the current models.
"""
from __future__ import annotations

import pytest

from app import _running_migration_cli


@pytest.mark.parametrize("argv", [
    ["flask", "db", "upgrade"],
    ["flask", "db", "current"],
    ["flask", "db", "migrate", "-m", "msg"],
    ["/usr/local/bin/flask", "db", "upgrade"],
    ["flask", "--app", "wsgi", "db", "upgrade"],
])
def test_detects_flask_db_invocations(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", argv)
    assert _running_migration_cli() is True


@pytest.mark.parametrize("argv", [
    ["gunicorn", "wsgi:app"],
    ["flask", "run"],
    ["flask", "--app", "wsgi", "run"],
    ["flask", "shell"],
    ["python", "-m", "pytest"],
    ["/usr/bin/python", "wsgi.py"],
    ["dbmanage", "db", "upgrade"],  # not the flask program
    [],
])
def test_ignores_non_flask_db_invocations(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", argv)
    assert _running_migration_cli() is False
