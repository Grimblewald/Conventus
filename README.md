# Conventus

Open-source conference management and web presence for learned societies.
Member portal, abstract submission and review, registration.  Flask +
SQLite.  Lightweight enough to deploy on a Pi or an old phone.

- Public pages, conferences, committee directory, announcements, contact form
- Member accounts (OTP-only sign-in — no passwords stored, ever)
- Per-conference registration & abstract submission + review
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

There is **no** branding baked in — the placeholder content reads "Name
Your Society" everywhere and is editable from the admin panel.

## Quick start (Cloudflare Tunnel)

The recommended path.  **No nginx, no certbot, no cert.pem, no port
forwarding** — just a domain on Cloudflare and a scoped API token.
Requires `cloudflared`, `curl`, `jq`, and `openssl`.

```bash
git clone https://github.com/your-org/conventus.git
cd conventus
chmod +x scripts/launch_cloudflared.sh
./scripts/launch_cloudflared.sh
```

The script handles everything interactively on first run — copies the
`.env` template, generates a `SECRET_KEY`, prompts for your domain,
a [scoped Cloudflare API token][deploy], your Account ID if needed,
and your SMTP settings so email works out of the box.  `cloudflared
tunnel login` is never used.

Visit the setup URL printed in the terminal, paste the one-time setup
password, and step through the wizard.  After that the admin panel is
yours.

Full walkthrough: [`docs/DEPLOY-CLOUDFLARE-SIMPLE.md`][deploy]

[deploy]: docs/DEPLOY-CLOUDFLARE-SIMPLE.md

## Local development

```bash
git clone https://github.com/your-org/conventus.git
cd conventus

# The launch script also works for local dev — just skip the Cloudflare
# token prompt (press Enter) and choose "console" for email (OTP codes
# print to the terminal).
chmod +x scripts/launch_cloudflared.sh
./scripts/launch_cloudflared.sh

# Or skip the script entirely and run gunicorn directly:
uv sync
cp .env.example .env
# Generate a SECRET_KEY and put it in .env, then:
uv run gunicorn --bind 127.0.0.1:5005 wsgi:app
```

Open http://127.0.0.1:5005. Every URL redirects to `/setup` until the
first-run wizard is complete.

## Reading the codebase

```
app/
├── __init__.py             # Flask app factory + setup-gate
├── config.py               # env-driven config (SQLite / Postgres)
├── extensions.py           # shared Flask-* singletons
├── models/                 # SQLAlchemy models, one file per domain
├── security/               # @requires_permission, audit logger
├── services/               # mail / payments / uploads / fonts / markdown
├── blueprints/
│   ├── auth/               # OTP sign-in (hardened)
│   ├── public/             # everything visitors can see
│   ├── member/             # logged-in member area
│   ├── admin/              # admin + committee panel (split by concern)
│   └── setup_wizard/       # first-run wizard
├── templates/              # Jinja2 templates
└── static/                 # site.css (vars-driven) + site.js
deploy/                     # systemd units, nginx config
scripts/                    # launch_cloudflared.sh, launch.sh, backup.py,
                            # healthcheck.py, admin_cli.py
docs/                       # install + deploy + security + updating
migrations/                 # Alembic/Flask-Migrate
tests/                      # pytest suite
placeholder.yaml            # seeded by the wizard on first run
.env.example                # secrets template
wsgi.py                     # gunicorn entry
CHANGELOG.md                # release notes
```

## Documentation index

- [`docs/INSTALL.md`](docs/INSTALL.md) — prerequisites, local dev setup
- [`docs/DEPLOY-CLOUDFLARE-SIMPLE.md`](docs/DEPLOY-CLOUDFLARE-SIMPLE.md) — zero-config Cloudflare tunnel (recommended)
- [`docs/DEPLOY-VPS.md`](docs/DEPLOY-VPS.md) — single VPS w/ systemd + nginx
- [`docs/SECURITY.md`](docs/SECURITY.md) — hardening notes
- [`docs/UPDATING.md`](docs/UPDATING.md) — keeping a deployment current
- [`docs/CUSTOMISING.md`](docs/CUSTOMISING.md) — what each admin panel does

## License

GPLv3 — Frithjof Herb, 2026
