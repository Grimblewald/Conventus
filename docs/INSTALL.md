# Installing on any host

Tested on Linux + macOS, Python 3.12+. Anything that can run `uv` and bind a
TCP port works — a tiny VPS, a Raspberry Pi at home behind a Cloudflare
tunnel, a NAS, your laptop.

## 1. Prerequisites

* **Python 3.12** (`python3 --version`)
* **uv** — fast resolver & runner. Install with `pip install uv` or follow
  the [official instructions](https://docs.astral.sh/uv/getting-started/installation/).
* A way to receive email — SMTP credentials, or just run with
  `MAIL_BACKEND=console` for local testing (OTPs print to stdout).
* **Optional:** Postgres if you expect bursty load (many registrations or
  abstracts in a short window); Redis if you'll run multiple Gunicorn
  workers and want rate-limit state shared between them.

## 2. Clone + configure

```bash
git clone <this repo> society-site
cd society-site

cp .env.example .env
$EDITOR .env
```

At minimum:

* Set `SECRET_KEY` to a real value. Refusing to start with the default is a
  feature, not a bug — generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
* Decide on `MAIL_BACKEND` (`console` for now is fine).
* Leave `DATABASE_URL` unset to use SQLite, or point at Postgres.

## 3. Install dependencies

```bash
uv sync
```

This creates a `.venv/`, installs everything from `pyproject.toml`, and
records a lockfile.

## 4. First launch

```bash
uv run gunicorn --bind 127.0.0.1:5005 wsgi:app
```

The server prints something like:

```
=========================================================================
 FIRST-RUN SETUP REQUIRED
=========================================================================
 Open the site and visit /setup. Use this one-time password:
     gJk7p3v...
 Stored at .../instance/setup-pw
 It will be DELETED automatically after setup completes.
=========================================================================
```

Open `http://127.0.0.1:5005`. Every URL redirects to `/setup` until
configuration is complete. Paste the password, step through the wizard
(admin email, site identity, palette, fonts), submit. The `setup-pw` file
is removed automatically and the wizard becomes permanently unreachable.

You are now logged in as the admin. The rest of customisation happens in
**Admin → Site → Palette / Fonts / Images / Identity** and the **Pages /
Navigation / Footer / Committee** sections.

## 5. Going to production

Pick a target:
- [Single VPS (systemd + nginx)](DEPLOY-VPS.md)
- [Cloudflare tunnel — zero-config](DEPLOY-CLOUDFLARE-SIMPLE.md)

Before going public, read [SECURITY.md](SECURITY.md) — it lists the env
vars that must be set, what to verify, and the hardening the app applies
for you.
