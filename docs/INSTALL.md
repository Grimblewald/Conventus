# Prerequisites + manual setup

The recommended path is the [`launch_cloudflared.sh`][launch] script
documented in the README — it handles `.env` creation, secret generation,
and all prompts interactively.  This page covers what you need installed
and the manual steps if you prefer to work without the script.

[launch]: ../scripts/launch_cloudflared.sh

## Prerequisites

* **Python 3.12** or later (`python3 --version`)
* **uv** — install with `pip install uv` or follow the
  [official instructions](https://docs.astral.sh/uv/getting-started/installation/)
* **cloudflared** (for the Cloudflare Tunnel path) — install from
  [Cloudflare's download page](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
* **tectonic** — the LaTeX engine that renders invoice/receipt/adjustment-note
  PDFs and the abstract booklet. Run `scripts/install-tectonic.sh` (installs to
  `~/.local/bin`, pre-warms the package cache). There is no fallback for a
  missing tectonic — those PDFs simply won't render until it's installed — so
  do this before going live even if you're not using the document system on day
  one.
* A way to receive email — SMTP credentials, or `MAIL_BACKEND=console` for
  local testing (OTPs print to the terminal)
* **Optional:** Postgres if you expect bursty load; Redis if you run
  multiple Gunicorn workers and want shared rate-limit state

## Manual setup (without the launch script)

```bash
git clone https://github.com/your-org/conventus.git
cd conventus

cp .env.example .env
$EDITOR .env
```

At minimum set in `.env`:

* `SECRET_KEY` — generate with:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
* `CLOUDFLARE_DOMAIN` — your domain
* `CLOUDFLARE_API_TOKEN` — scoped token (see [DEPLOY-CLOUDFLARE-SIMPLE.md](DEPLOY-CLOUDFLARE-SIMPLE.md))
* `MAIL_BACKEND=console` for local dev, or SMTP settings for real email

Then install dependencies, tectonic, and launch:

```bash
uv sync
scripts/install-tectonic.sh
uv run gunicorn --bind 127.0.0.1:5005 wsgi:app
```

Open `http://127.0.0.1:5005`. Every URL redirects to `/setup` until the
first-run wizard is complete.  A one-time setup password is printed to the
console — paste it into the wizard.

## Going to production

- [Cloudflare Tunnel — zero-config (the supported path)](DEPLOY-CLOUDFLARE-SIMPLE.md)

Before going public, read [SECURITY.md](SECURITY.md) — it lists the env
vars that must be set for production and the hardening the app applies
for you.

Sizing: default gunicorn settings give `3 workers × 32 threads`. For
registration spikes, raise via `GUNICORN_WORKERS` / `GUNICORN_THREADS` in
`.env`, and consider Postgres (`DATABASE_URL`) if SQLite logs
`database is locked`.
