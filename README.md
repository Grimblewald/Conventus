# Conventus

Open-source conference management and web presence for learned societies.
Member portal, abstract submission and review, registration.  Flask +
SQLite.  Lightweight enough to deploy on a Pi or an old phone.

- Public pages, conferences, committee directory, announcements, contact form
- Member accounts (OTP-only sign-in — no passwords stored, ever)
- Per-conference registration & abstract submission + review
- Abstract booklet compilation (LaTeX source zip with per-abstract folders,
  header/footer/background images, portrait layout, dynamic figure sizing)
- An admin panel covering **every** customisable surface of the site:
  - **Site → Identity / Palette / Fonts / Images** (no developer required)
  - **Pages** — Markdown-bodied CMS pages with stable slugs
  - **Navigation** & **Footer** editors
  - **Committee** — portraits, ORCID/Scholar links, drag-style reorder
  - **Conferences** — incl. per-conference price tiers, sponsor tiers,
    booklet imagery, organising committee
  - **Announcements**
  - **Members** + per-role **Permissions** matrix
  - **Audit log**
  - **System → Backup** — full-site zip download & OTP-gated restore
    with chunked transfer for large files
  - **System → Update** — OTP-gated git pull + migration from the admin panel
- Hardened OTP login, CSRF on every form, CSP via Talisman, image
  validation, rate-limited OTP issuance, attempt counter + lockout
- Postgres or SQLite via `DATABASE_URL`; Redis-backed rate limiting is opt-in
- One-command updates: `uv run python -m app update` (backs up DB, pulls
  latest code, runs migrations, restarts the service)

There is **no** branding baked in — the placeholder content reads "Name
Your Society" everywhere and is editable from the admin panel.

## Setting up on a VPS

The recommended host is a small Linux VPS rented from **[Binary Lane][bl]** as its VPS instances ship Ubuntu with `python3`, `git`, `curl`, `jq`, and
`openssl` pre-installed.

1. **Install cloudflared**

   Follow the official package repository setup at
   **[pkg.cloudflare.com][cfpkg]**, then:

   ```bash
   sudo apt install cloudflared
   ```

2. **Install uv** (if not already present)

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Clone and set up**

   ```bash
   git clone https://github.com/Grimblewald/Conventus.git
   cd Conventus
   chmod +x scripts/update.sh
   ```

4. **Launch the site**

   ```bash
   uv run python -m app launch
   ```

   On first run the script walks you through hostname, API token, and
   mail settings.  It writes everything to `.env` and prints a
   one-time setup password — visit your domain and step through the
   wizard.  After that the admin panel is yours.

5. **Register service** (so the site starts on boot)

   ```bash
   uv run python -m app register-service
   ```

[bl]: https://www.binarylane.com.au/vps-hosting/linux-vps
[cfpkg]: https://pkg.cloudflare.com

### Commands

| Command | What it does |
|---|---|
| `uv run python -m app launch` | Start gunicorn + Cloudflare tunnel |
| `uv run python -m app update` | Backup DB → `git pull` → migrate → restart service |
| `uv run python -m app revert` | Restore last backup → git reset → restart |
| `uv run python -m app backup` | Manual database + uploads backup |
| `uv run python -m app syncpages` | Sync `content/pages/*.md` to the database |
| `uv run python -m app register-service` | Install systemd user units |
| `uv run python -m app uninstall-service` | Remove systemd user units |

The `update` command is designed to be run while the site is live.
Backups are stored in `var/backups/`.  If an update goes sideways, `revert`
restores the exact state from before the last `update`.

### Managing page content with git

Static pages (Privacy Policy, Terms & Conditions, Code of Conduct, etc.)
are stored as Markdown files in `content/pages/`.  The filename (minus the
`.md` extension) becomes the page slug on the live site.  Edit them
locally, track them in git, and sync to the database with one command:

```bash
uv run python -m app syncpages
```

This uses the admin API to create or update every page listed in the
directory.  No manual copy-paste into the admin UI required.

## Local development

```bash
git clone https://github.com/your-org/conventus.git
cd Conventus
uv sync
cp .env.example .env
# Generate a SECRET_KEY and put it in .env, then:
    python wsgi.py
```

Open http://127.0.0.1:5005. Every URL redirects to `/setup` until the
first-run wizard is complete.

For OTP testing, set `MAIL_BACKEND=console` in `.env` — codes print to
the terminal.

## Tests

```bash
uv sync --extra dev
uv run pytest
uv run ruff check app/
```

## Reading the codebase

```
app/
├── __init__.py             # Flask app factory + setup-gate
├── __main__.py             # CLI dispatcher (launch / update / revert / …)
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
scripts/                    # launch_cloudflared.sh, launch.sh, update.sh,
                            # backup.py, healthcheck.py, admin_cli.py
migrations/                 # Alembic/Flask-Migrate
tests/                      # pytest suite
placeholder.yaml            # seeded by the wizard on first run
.env.example                # secrets template
wsgi.py                     # gunicorn entry
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
