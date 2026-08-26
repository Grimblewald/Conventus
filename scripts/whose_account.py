#!/usr/bin/env python3
"""Explain why a member signs in and sees none of their own work.

Runs the member dashboard's own queries for an address, then the same queries
with the filters removed, and prints the difference. Whatever the dashboard is
dropping shows up as a row present in the second list and absent from the
first, with the reason beside it.

Read-only — it opens the database and prints. Nothing is written.

Usage:  uv run python scripts/whose_account.py someone@example.org
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.models.user import User                              # noqa: E402
from app.models.abstract import Abstract                      # noqa: E402
from app.models.registration import Registration              # noqa: E402


def _why_hidden(row, user) -> str:
    """The dashboard's filters, restated as the reason a row is missing."""
    reasons = []
    if row.user_id != user.id:
        reasons.append(f"owned by user {row.user_id}, not {user.id}")
    if row.deleted_at is not None:
        reasons.append(f"soft-deleted {row.deleted_at}")
    return "; ".join(reasons) or "shown"


def report(raw: str) -> int:
    wanted = raw.strip()

    # Case-insensitive, unlike the sign-in lookup. Not because that mismatch is
    # expected, but because an address failing to match here at all is a
    # different fault from the one being investigated and is worth separating.
    users = [u for u in User.query.all()
             if (u.email or "").lower() == wanted.lower()]

    if not users:
        print(f"No account for {wanted!r}.")
        return 1

    if len(users) > 1:
        print(f"NOTE: {len(users)} accounts hold this address: "
              + ", ".join(f"{u.id}={u.email}" for u in users) + "\n")

    for user in users:
        print(f"Account {user.id}  {user.email}  role={user.role_name or '-'}"
              + ("  ACCOUNT SOFT-DELETED" if user.deleted_at else ""))

        # Everything owned, before the dashboard's filters.
        regs = Registration.query.filter_by(user_id=user.id).all()
        abs_ = Abstract.query.filter_by(user_id=user.id).all()

        if not regs and not abs_:
            print("    owns no registrations and no abstracts\n")
            continue

        print(f"    registrations: {len(regs)}")
        for r in regs:
            print(f"      {r.id:<5} {r.reference:<14} {r.status:<10} "
                  f"conf={r.conference_id:<4} {_why_hidden(r, user)}")

        print(f"    abstracts: {len(abs_)}")
        for a in abs_:
            print(f"      {a.id:<5} {(a.title or '')[:38]:<38} {a.status:<10} "
                  f"conf={a.conference_id:<4} {_why_hidden(a, user)}")

        hidden = ([r for r in regs if r.deleted_at]
                  + [a for a in abs_ if a.deleted_at])
        if hidden:
            print(f"\n    ** {len(hidden)} row(s) are soft-deleted. The "
                  f"dashboard hides these;\n       the pages that open one by "
                  f"id do not, so they stay reachable\n       by direct link. "
                  f"This is what an empty dashboard looks like\n       when the "
                  f"rows are still in the table.")
        print()

    # Rows nobody can reach: entered without an owner attached.
    orphan_a = Abstract.query.filter_by(user_id=None).count()
    orphan_r = Registration.query.filter_by(user_id=None).count()
    if orphan_a or orphan_r:
        print(f"Unowned across the site: {orphan_a} abstract(s), "
              f"{orphan_r} registration(s). These show on no dashboard.")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    app = create_app()
    with app.app_context():
        raise SystemExit(report(sys.argv[1]))
