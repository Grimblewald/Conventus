# Society Site

A reusable, blank-canvas Flask website + conference management system for
academic societies. Drop it on any small VPS or even a Raspberry Pi, run
the first-run setup wizard, and you have a working society site with:

- Public pages, conferences, committee directory, announcements, contact form
- Member accounts (OTP-only sign-in — no passwords stored, ever)
- Per-conference registration & abstract submission
- An admin panel covering **every** customisable surface of the site:
  - **Site → Identity / Palette / Fonts / Images** (no developer required)
  - **Pages** — Markdown-bodied CMS pages with stable slugs
  - **Navigation** & **Footer** editors
  - **Committee** — portraits, ORCID/Scholar links, drag-style reorder
  - **Conferences** — incl. per-conference price tiers
  - **Announcements**
  - **Members** + per-role **Permissions** matrix
  - **Audit log**
- Hardened OTP login, CSRF on every form, CSP via Talisman, image
  validation, rate-limited OTP issuance, attempt counter + lockout
- Postgres or SQLite via `DATABASE_URL`; Redis-backed rate limiting is opt-in
- Designed to be updated with `git pull` — see `docs/UPDATING.md`

There is **no** branding baked in — the placeholder content reads "Your
Society" everywhere and is editable from the admin panel.

## Quick start (local)

```bash
git clone <this repo>
cd <repo>

# Configure secrets
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Put that into SECRET_KEY in .env.

uv sync
uv run gunicorn --bind 127.0.0.1:5005 wsgi:app
```

Open http://127.0.0.1:5005. The first request creates `instance/setup-pw`
and prints a one-time setup password to the console — paste it into the
wizard at `/setup`. Complete the wizard; the password file is deleted
automatically and `/setup` becomes inaccessible.

## Deploy to the internet (Cloudflare Tunnel)

The recommended production path is a VPS + Cloudflare Tunnel.  It requires
**no nginx, no certbot, no port forwarding** — just a domain on Cloudflare.

```bash
# 1. Run the launcher — it handles everything interactively:
#    - copies .env from .env.example if it doesn't exist
#    - prompts for your domain, API token, and email settings
#    - generates a SECRET_KEY automatically
#    - saves everything to .env so the next run skips the prompts
chmod +x scripts/launch_cloudflared.sh
./scripts/launch_cloudflared.sh
```

Full walkthrough: [`docs/DEPLOY-CLOUDFLARE-SIMPLE.md`](docs/DEPLOY-CLOUDFLARE-SIMPLE.md)

## Reading the codebase

```
app/
├── __init__.py             # Flask app factory + setup-gate
├── config.py               # env-driven config (SQLite / Postgres)
├── extensions.py           # shared Flask-* singletons
├── models/                 # SQLAlchemy models, one file per domain
├── security/               # @requires_permission, audit logger
├── services/               # mail / uploads / fonts / markdown / targets
├── blueprints/
│   ├── auth/               # OTP sign-in (hardened)
│   ├── public/             # everything visitors can see
│   ├── member/             # logged-in member area
│   ├── admin/              # admin + committee panel (split by concern)
│   └── setup_wizard/       # first-run wizard
├── templates/              # Jinja2 templates
└── static/                 # site.css (vars-driven) + site.js
deploy/                     # systemd units, nginx config
scripts/                    # launch.sh, launch_cloudflared.sh, admin_cli.py
docs/                       # install + deploy + security + updating
placeholder.yaml            # seeded by the wizard the first time the app runs
.env.example                # secrets template
wsgi.py                     # gunicorn entry
```

## Documentation index

- [`docs/INSTALL.md`](docs/INSTALL.md) — fresh install on any host
- [`docs/DEPLOY-VPS.md`](docs/DEPLOY-VPS.md) — single VPS w/ systemd + nginx
- [`docs/DEPLOY-CLOUDFLARE-SIMPLE.md`](docs/DEPLOY-CLOUDFLARE-SIMPLE.md) — zero-config Cloudflare tunnel
- [`docs/SECURITY.md`](docs/SECURITY.md) — hardening notes
- [`docs/UPDATING.md`](docs/UPDATING.md) — keeping a deployment current
- [`docs/CUSTOMISING.md`](docs/CUSTOMISING.md) — what each admin panel does

## License

GPLv3
