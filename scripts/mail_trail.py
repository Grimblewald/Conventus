#!/usr/bin/env python3
"""What the site can tell you about email sent to one address.

There is no mail log: a successful send writes nothing anywhere. What exists
is indirect, and this gathers it in one place.

The useful one is the sign-in code. A code row is only written after the SMTP
relay has accepted the message, so a row is evidence the handoff succeeded —
and `attempt_count` says whether anyone ever typed that code back in, which is
the difference between "not delivered" and "not used".

Read-only — it opens the database and prints. Nothing is written, and no code
is printed: an unexpired one is a live credential.

Usage:  uv run python scripts/mail_trail.py someone@example.org
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.models import PaymentEvent, Registration, User       # noqa: E402
from app.models.audit import AuditLog                         # noqa: E402
from app.models.otp import OTPCode                            # noqa: E402

# Ledger events that mean "we sent this person something".
MAIL_EVENTS = ("registration.payment_email_sent", "invoice.sent",
               "document.sent")


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"


def _outcome(otp, nxt) -> str:
    """What became of one issued code.

    Attempts are the tell. A code nobody typed into was either never received
    or never wanted; a code with attempts against it reached somebody.
    """
    if otp.consumed_at and otp.attempt_count:
        return f"USED — entered, {otp.attempt_count} attempt(s)"
    if otp.consumed_at and nxt is not None:
        return "superseded by the next request (never entered)"
    if otp.consumed_at:
        return "consumed, no attempts recorded"
    if otp.attempt_count:
        return f"reached them — {otp.attempt_count} wrong attempt(s), not used"
    if otp.expires_at < datetime.utcnow():
        return "expired, never entered"
    return "outstanding, never entered"


def report(raw: str) -> int:
    wanted = raw.strip().lower()

    user = next((u for u in User.query.all()
                 if (u.email or "").lower() == wanted), None)

    print(f"Address: {wanted}")
    if user is None:
        print("  No account. Codes may still have been issued — sign-in "
              "creates the account only once a code is entered.\n")
    else:
        locked = (user.locked_until and user.locked_until > datetime.utcnow())
        print(f"  Account {user.id}  role={user.role_name or '-'}  "
              f"last login {_fmt(user.last_login_at)}"
              + ("  ACCOUNT SOFT-DELETED" if user.deleted_at else ""))
        if locked:
            print(f"  LOCKED OUT until {_fmt(user.locked_until)} — too many "
                  f"wrong codes. They cannot request a new one until then.")
        print()

    codes = (OTPCode.query
             .filter(OTPCode.email == wanted)
             .order_by(OTPCode.id)
             .all())

    print(f"Sign-in / verification codes issued: {len(codes)}")
    print("  Each row means the SMTP relay accepted the message.")
    if not codes:
        print("  None. Either none was ever requested, or every attempt "
              "failed before a row was written — check the app log for "
              "\"send_mail(... ) failed\", and consider the rate limit "
              "(8/hour, 3/minute on login) which blocks the request before "
              "any send is attempted.")
    else:
        print(f"    {'issued':<20} {'purpose':<14} {'expires':<20} "
              f"{'ip':<16} outcome")
        by_purpose: dict[str, list] = {}
        for c in codes:
            by_purpose.setdefault(c.purpose, []).append(c)
        for c in codes:
            same = by_purpose[c.purpose]
            i = same.index(c)
            nxt = same[i + 1] if i + 1 < len(same) else None
            print(f"    {_fmt(c.created_at):<20} {c.purpose:<14} "
                  f"{_fmt(c.expires_at):<20} {(c.ip or '-'):<16} "
                  f"{_outcome(c, nxt)}")

        touched = [c for c in codes if c.attempt_count]
        print()
        if touched:
            print(f"  {len(touched)} of {len(codes)} code(s) had an attempt "
                  f"against them, so those messages reached them.")
        else:
            print("  No code was ever typed back in. Consistent with the mail "
                  "not arriving — but equally with never trying.")

    logins = (AuditLog.query
              .filter(AuditLog.actor_email == wanted)
              .order_by(AuditLog.id.desc())
              .limit(20)
              .all())
    print()
    print(f"Recent audit entries for this address: {len(logins)}")
    for a in logins:
        print(f"    {_fmt(a.created_at):<20} {a.action:<24} "
              f"{(a.ip or '-'):<16} {(a.summary or '')[:60]}")

    if user is not None:
        reg_ids = [r.id for r in Registration.query
                   .filter_by(user_id=user.id).all()]
        sends = []
        if reg_ids:
            sends = (PaymentEvent.query
                     .filter(PaymentEvent.registration_id.in_(reg_ids),
                             PaymentEvent.event_type.in_(MAIL_EVENTS))
                     .order_by(PaymentEvent.id)
                     .all())
        print()
        print(f"Recorded sends of registration/invoice mail: {len(sends)}")
        for e in sends:
            print(f"    {_fmt(e.created_at):<20} {e.event_type:<32} "
                  f"{(e.note or '')[:50]}")

    print()
    print("Not knowable from here:")
    print("  - Whether any message was delivered to the inbox. A send counts "
          "as successful once the relay accepts it; a later bounce, a spam "
          "filing or a silent drop leaves no trace on this side.")
    print("  - Anything about abstract confirmation emails. Those record "
          "nothing at all, sent or failed.")
    print("  Both of those live in the SMTP provider's own logs, by "
          "recipient and timestamp.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    app = create_app()
    with app.app_context():
        raise SystemExit(report(sys.argv[1]))
