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

Usage:
  uv run python scripts/mail_trail.py someone@example.org
  uv run python scripts/mail_trail.py --unreached [--domains]
  uv run python scripts/mail_trail.py --abuse

`--unreached` lists every address that was issued codes and never once typed
one back in. One such address is a person who gave up; a whole recipient
domain of them is mail that is not arriving. `--domains` prints only the
per-domain summary.

`--abuse` asks the opposite question: not who failed to receive mail, but who
was sent it without asking. Every verification code goes to an address typed
in by whoever submitted the form, so the form can be driven as a way of
mailing strangers. This counts the volume, names the sources, and shows the
shape over time.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.models import PaymentEvent, Registration, User       # noqa: E402
from app.models.audit import AuditLog                         # noqa: E402
from app.models.otp import OTPCode                            # noqa: E402

# Ledger events that mean "we sent this person something".
MAIL_EVENTS = ("registration.payment_email_sent", "invoice.sent",
               "document.sent")


def _now() -> datetime:
    """Naive UTC, matching how every timestamp in this schema is stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    if otp.expires_at < _now():
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
        locked = (user.locked_until and user.locked_until > _now())
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


def unreached(domains_only: bool = False) -> int:
    """Every address issued a code that was never once typed back in.

    An attempt is the only positive evidence this side of the network that a
    message arrived. Its absence across every code an address was ever sent is
    not proof of non-delivery — somebody may simply have changed their mind —
    but it is the shape non-delivery takes, and at whole-domain scale the
    coincidence stops being plausible.
    """
    codes = OTPCode.query.order_by(OTPCode.id).all()
    if not codes:
        print("No codes have ever been issued.")
        return 0

    issued: dict[str, list] = defaultdict(list)
    for c in codes:
        issued[(c.email or "").lower()].append(c)

    # Signed in at least once, by any route — the unambiguous proof of receipt.
    signed_in = {(u.email or "").lower() for u in User.query.all()
                 if u.last_login_at is not None}

    stuck = []
    for email, rows in issued.items():
        if any(c.attempt_count for c in rows):
            continue
        if email in signed_in:
            # Attempts are only counted against the newest unconsumed code, so
            # an old success can leave every surviving row untouched.
            continue
        stuck.append((email, rows))

    by_domain: dict[str, list] = defaultdict(list)
    for email, rows in stuck:
        by_domain[email.rpartition("@")[2] or "(no domain)"].append((email, rows))

    reached_domains: dict[str, int] = defaultdict(int)
    for email, rows in issued.items():
        if any(c.attempt_count for c in rows) or email in signed_in:
            reached_domains[email.rpartition("@")[2] or "(no domain)"] += 1

    print(f"Addresses ever issued a code: {len(issued)}")
    print(f"Never once entered one:       {len(stuck)}")
    print()
    print("By recipient domain, worst first. 'reached' is addresses on the "
          "same domain that did get in —")
    print("a domain with none is the one to be suspicious of.")
    print()
    print(f"  {'domain':<34} {'stuck':>6} {'reached':>8}  {'codes':>6}")
    order = sorted(by_domain.items(),
                   key=lambda kv: (reached_domains[kv[0]], -len(kv[1])))
    for domain, entries in order:
        total_codes = sum(len(r) for _, r in entries)
        flag = "  <-- nobody on this domain has ever signed in" \
            if not reached_domains[domain] and len(entries) > 1 else ""
        print(f"  {domain:<34} {len(entries):>6} "
              f"{reached_domains[domain]:>8}  {total_codes:>6}{flag}")

    if not domains_only:
        print()
        print("Addresses:")
        for domain, entries in order:
            print(f"  {domain}")
            for email, rows in sorted(entries, key=lambda e: -len(e[1])):
                first = min(c.created_at for c in rows)
                last = max(c.created_at for c in rows)
                span = "" if first == last else f" .. {_fmt(last)}"
                print(f"    {email:<44} {len(rows):>2} code(s)  "
                      f"{_fmt(first)}{span}")

    print()
    print("An address here was sent codes the relay accepted and never used "
          "one. Read it with the")
    print("domain column: scattered singles are ordinary abandonment; a "
          "domain where nobody has ever")
    print("got in is a delivery problem, and the recipient's own mail admin "
          "can confirm it from a trace.")
    return 0


def abuse() -> int:
    """Verification mail sent to people who never asked for it.

    A code issued to an address with no account here, never entered, is a
    message nobody wanted. A handful is ordinary — somebody mistyping, or
    thinking better of it. Thousands, from two addresses, around the clock,
    is the form being used to mail strangers, and every one of them was sent
    over the society's own name.
    """
    codes = OTPCode.query.order_by(OTPCode.id).all()
    if not codes:
        print("No codes have ever been issued.")
        return 0

    members = {(u.email or "").lower() for u in User.query.all()}
    by_purpose: dict[str, list] = defaultdict(list)
    for c in codes:
        by_purpose[c.purpose].append(c)

    print("Codes issued, by purpose:")
    for purpose, rows in sorted(by_purpose.items(), key=lambda kv: -len(kv[1])):
        unasked = [c for c in rows
                   if not c.attempt_count
                   and (c.email or "").lower() not in members]
        print(f"  {purpose:<24} {len(rows):>6} issued   "
              f"{len(unasked):>6} to strangers, never entered")

    for purpose, rows in sorted(by_purpose.items(), key=lambda kv: -len(kv[1])):
        sources = defaultdict(list)
        for c in rows:
            sources[c.ip or "-"].append(c.created_at)
        if len(rows) < 50:
            continue

        print()
        print(f"=== {purpose} ===")
        targets = {(c.email or "").lower() for c in rows}
        entered = [c for c in rows if c.attempt_count]
        print(f"  messages           : {len(rows)}")
        print(f"  distinct recipients: {len(targets)}")
        print(f"  ever entered       : {len(entered)}")
        print(f"  recipients with no account here: "
              f"{len(targets - members)}")
        print(f"  period             : {min(c.created_at for c in rows):%Y-%m-%d} "
              f".. {max(c.created_at for c in rows):%Y-%m-%d}")

        print(f"  sources ({len(sources)}), busiest first:")
        print(f"    {'ip':<42} {'msgs':>6}  active window")
        for ip, times in sorted(sources.items(), key=lambda kv: -len(kv[1]))[:12]:
            print(f"    {ip:<42} {len(times):>6}  "
                  f"{min(times):%Y-%m-%d %H:%M} .. {max(times):%Y-%m-%d %H:%M}")

        # Grouped by network: rotating addresses inside one is exactly what
        # defeats a limit keyed on the caller.
        nets = defaultdict(int)
        for c in rows:
            ip = c.ip or "-"
            nets["%s.0/24" % ".".join(ip.split(".")[:3])
                 if "." in ip else ip] += 1
        top = sorted(nets.items(), key=lambda kv: -kv[1])[:5]
        print("  busiest networks:")
        for net, n in top:
            share = 100 * n // max(len(rows), 1)
            print(f"    {net:<42} {n:>6}  ({share}% of all {purpose} mail)")

        weeks = defaultdict(int)
        for c in rows:
            iso = c.created_at.isocalendar()
            weeks[f"{iso[0]}-W{iso[1]:02d}"] += 1
        print("  weekly volume:")
        peak = max(weeks.values())
        for week in sorted(weeks):
            n = weeks[week]
            bar = "#" * max(1, (n * 48) // peak) if n else ""
            print(f"    {week}  {n:>6}  {bar}")

        heavy = defaultdict(int)
        for c in rows:
            heavy[(c.email or "").lower()] += 1
        worst = sorted(heavy.items(), key=lambda kv: -kv[1])[:8]
        if worst and worst[0][1] > 2:
            print("  most-targeted recipients:")
            for email, n in worst:
                print(f"    {email:<48} {n:>4} message(s)")

    print()
    print("A recipient with no account here who never entered a code did not "
          "ask for one. Read the")
    print("source column: real use is spread thin across many addresses, so a "
          "single network holding")
    print("most of the volume is someone driving the form.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        raise SystemExit(2)
    app = create_app()
    with app.app_context():
        if args[0] == "--unreached":
            raise SystemExit(unreached("--domains" in args))
        if args[0] == "--abuse":
            raise SystemExit(abuse())
        if len(args) != 1:
            print(__doc__)
            raise SystemExit(2)
        raise SystemExit(report(args[0]))
