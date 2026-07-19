# Security notes

Everything you should know about the app's security posture, what it
does for you, and what's still your problem.

## Refuses to start with developer-default secrets

`app/config.py` checks `SECRET_KEY` at boot and raises a `RuntimeError`
if it's unset or still the placeholder string. Don't try to work around
it — if a session cookie can be forged, every admin tool in the site is
the attacker's.

## What's baked in

| Concern                              | How it's handled                                                                 |
| ------------------------------------ | -------------------------------------------------------------------------------- |
| CSRF                                 | `flask-wtf` `CSRFProtect`; every form includes `{{ csrf_token() }}`              |
| Security headers + HTTPS             | `flask-talisman` — strict CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors 'none'` |
| Sign-in throttle                     | `flask-limiter` on `/auth/login` and `/auth/verify` (per-IP)                     |
| Contact-form throttle                | per-IP limiter + honeypot field                                                  |
| OTP brute-force                      | `OTP_MAX_ATTEMPTS` per code; over → code burned + user `locked_until`            |
| Prior unconsumed codes               | Invalidated on new issuance per email                                            |
| Login OTP vs. destructive-action OTP | `OTPCode.purpose` separates them — a login code can't authorise a delete       |
| Session cookies                      | `Secure` + `HttpOnly` + `SameSite=Lax` (production)                              |
| Uploads                              | Pillow `verify()` + EXIF strip + re-encode for images; `%PDF-` magic for PDFs    |
| Decompression bombs                  | `Image.MAX_IMAGE_PIXELS` cap + per-route byte cap                                |
| Soft deletes                         | Conferences, abstracts, registrations, announcements, pages, committee, users all soft-delete |
| Audit log                            | `app/security/audit.py` records every admin / committee mutation                  |
| Static-fonts CSP                     | We ship no external font loads, so the CSP forbids `font-src` beyond `'self'`    |
| Inline scripts                       | CSP `script-src` carries SHA-256 hashes computed from templates at startup — no `unsafe-inline`. Inline scripts must be static; page data goes in `<script type="application/json">` blocks |

## Payments

Card handling is delegated entirely to ANZ Worldline's Hosted Checkout
(PCI SAQ-A scope) — no card data ever reaches the server, and the
codebase stores none.

| Concern                    | How it's handled                                                       |
| -------------------------- | ---------------------------------------------------------------------- |
| API credentials at rest    | Fernet-encrypted in the DB with a key derived from `SECRET_KEY` — rotating `SECRET_KEY` invalidates them (re-enter in the Financial panel) |
| Webhooks                   | HMAC-SHA256 signature verification via the provider SDK before any processing; invalid signatures are ignored |
| Going live                 | OTP-confirmed toggle; disabling or re-enabling the gateway always resets to sandbox |
| Member exposure            | Members can only reach checkout when gateway enabled **and** live **and** the member-payments switch is open — every other state shows "under construction" |
| Status integrity           | Stale/out-of-order failure events never downgrade a paid registration; duplicate captures (possible double payments), disputes, and failed refunds email admins and are audit-logged |
| Traceability               | Every verified gateway event is appended to a per-transaction ledger (Admin → Financial → Transactions) |

## Backups

* Regular backups (admin download, `scripts/backup.py`, and the scheduled
  systemd timer) never include `.env`.
* The optional **full backup** bundles `.env` and is only available from
  the admin panel: OTP-confirmed, AES-256 encrypted with a password chosen
  at creation and stored nowhere. That archive can clone the entire site
  — including decrypting stored payment credentials — so store it offline
  and keep the password separate.
* Restores are OTP-gated and take an automatic safety backup first.

## What's your problem

* **Real TLS.** The supported deployment (Cloudflare Tunnel) terminates
  TLS for you. If you front the app any other way, put a TLS-terminating
  proxy in front — the Talisman defaults assume TLS is ahead of you.
* **Backup custody.** The built-in backup system produces the archives;
  getting them off the box and practising restores is on you. Keep `.env`
  (or a password-protected full backup) somewhere safe — without
  `SECRET_KEY`, stored payment credentials in a restored DB are
  unrecoverable by design.
* **The deploy host.** Patch it. Don't expose `instance/` over a static
  file handler. Make sure `flask.log` isn't world-readable — OTP codes
  printed by the `console` mail backend would leak otherwise (don't use
  that backend in production anyway).
* **Email reputation.** If sign-in OTPs land in spam, members can't log
  in. Use SPF + DKIM + DMARC on your sending domain.

## Logging hygiene

The console mail backend prints OTPs to stdout. **Never run in production
with `MAIL_BACKEND=console`.** Use SMTP, and confirm your log shipping
doesn't capture stdout from a misconfigured staging instance.

## Bursty-load notes

500 registrants making many requests over a month implies:

* SQLite is *probably* fine if writes are serialised, but you'll be safer
  on Postgres — set `DATABASE_URL` and migrate.
* Pin `GUNICORN_WORKERS=cpu_count + 1` and `GUNICORN_THREADS=32-64`. The
  `gthread` worker class handles 500 concurrent slow clients on a single
  CPU just fine.
* Move `RATELIMIT_STORAGE_URI` to Redis. Otherwise per-worker memory
  storage lets a parallelised attacker bypass per-IP limits.
* Watch `db.session.get(User, …)` query times. The `users.email` index is
  already there; the slow paths under load are usually OTP table
  ballooning (the consumed-at-cleanup task is on the roadmap — for now,
  prune manually if needed: `DELETE FROM otp_codes WHERE consumed_at < now() - interval '30 days'`).

## Reporting

Bugs that affect security should go to the admin email configured at
setup; everything else can be a normal issue.
