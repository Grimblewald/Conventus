"""CLI: promote/demote admins on a running deployment.

Run with:
    uv run python scripts/admin_cli.py
or:
    .venv/bin/python scripts/admin_cli.py

Filesystem access to the host is the only thing protecting this — make sure
the deploy directory isn't world-readable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the project importable when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.user import ensure_roles_exist  # noqa: E402


def list_admins():
    admins = (User.query.filter_by(role_name="admin")
              .filter(User.deleted_at.is_(None))
              .order_by(User.email).all())
    if not admins:
        print("  (no admins)")
    for i, u in enumerate(admins):
        print(f"  [{i}] {u.email}  —  {u.full_name or '(no name)'}")
    return admins


def add_admin():
    email = input("Email to promote/create as admin: ").strip().lower()
    if not email:
        return
    u = User.query.filter_by(email=email).first()
    if u:
        u.role_name = "admin"
        if u.deleted_at:
            u.deleted_at = None
        print(f"Promoted existing user {email} to admin.")
    else:
        name = input("Full name (optional): ").strip() or None
        u = User(email=email, full_name=name, role_name="admin")
        db.session.add(u)
        print(f"Created new admin {email}.")
    db.session.commit()


def remove_admin():
    admins = list_admins()
    if not admins:
        return
    raw = input("Index to demote to member (blank to cancel): ").strip()
    if not raw:
        return
    try:
        u = admins[int(raw)]
    except (ValueError, IndexError):
        print("Invalid index.")
        return
    u.role_name = "member"
    db.session.commit()
    print(f"Demoted {u.email} to member.")


def main():
    app = create_app()
    with app.app_context():
        ensure_roles_exist()
        while True:
            print("\n=== Admin manager ===")
            print("  1) List admins")
            print("  2) Add admin (or promote existing user)")
            print("  3) Remove admin (demote to member)")
            print("  q) Quit")
            choice = input("> ").strip().lower()
            if choice == "1":
                list_admins()
            elif choice == "2":
                add_admin()
            elif choice == "3":
                remove_admin()
            elif choice in ("q", "quit", "exit"):
                break


if __name__ == "__main__":
    main()
