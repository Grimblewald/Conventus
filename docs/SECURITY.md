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

## What's your problem

* **Real TLS.** Reverse-proxy with nginx + certbot, Caddy (which does it
  automatically), or sit behind Cloudflare. The Talisman defaults assume
  TLS is in front of you.
* **Backups.** Snapshot `instance/` and `uploads/` regularly. SQLite is
  one file; Postgres has `pg_dump`. Practise restoring.
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
