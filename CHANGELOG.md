# Changelog

## [0.3.0] — 2026-07-21  **MIGRATION REQUIRED**

### Added
- PDF document system: invoice, receipt, and adjustment-note PDFs compiled
  by tectonic from one curated, in-repo LaTeX skeleton
  (`app/latex/document.tex`) and attached to plaintext emails — one
  renderer (`app/services/documents.py::render_document`), used by preview,
  send, and the warm boot cache alike
  - `DocumentTemplate` model (`kind` = `invoice` | `receipt` | `adjustment`),
    replacing the old single `invoice_template`; each kind holds its email
    cover, PDF body, and business/tax fields independently
  - Admin template editor gains a **PDF body** section and a **Download
    preview** button; unset variables render as their bold field name
    (never a guessed `$0.00`), so previews are never mistaken for real data
  - A warm pregen cache compiles the all-placeholder preview at boot and on
    every template save, so "Download preview" serves instantly instead of
    a cold compile; a process-wide compile queue (`DOC_COMPILE_WORKERS`)
    caps concurrent tectonic processes and reports queue position back to
    the requester
  - `scripts/install-tectonic.sh` — idempotent installer: fetches tectonic
    to `~/.local/bin`, pre-warms its package cache against the document
    skeleton's package set, and fails loudly on a broken toolchain
  - The admin Financial dashboard surfaces a **PDF documents** status line
    (OK / loud warning) so a missing or broken tectonic is never silent —
    there is deliberately no plain-format fallback

### Changed
- **MIGRATION:** `invoice_template` is replaced by `document_template`
  (one row per kind); the migration copies the existing invoice row across
  and seeds `receipt`/`adjustment` defaults — run `db upgrade` once.
- Emails sent from a template may now carry a PDF attachment; **HTML email
  is removed** (`send_mail`'s `html`/`add_alternative` path is gone — every
  send is plaintext body + optional attachment)

## [0.2.0] — 2026-07-20  **MIGRATION REQUIRED**

### Added
- Online payments: ANZ Worldline Hosted Checkout (PCI SAQ-A), admin
  Financial panel (provider config, OTP-gated sandbox/live switch, member
  payments open/close), invoice template + automatic invoice/refund
  emails, manual Send Invoice with To/CC, admin test payments and test
  invoices, per-transaction event ledger with search
- Webhook processing with HMAC verification, guarded status transitions,
  refund/dispute/double-payment admin alerts, API key expiry warnings
- Unified backup archive format (manifest with git commit + migration
  head) shared by the admin panel and CLI; optional OTP-gated, AES-256
  password-protected full backup including `.env`
- Admin update flow queues the restart and shows a polling
  "restarting" page instead of racing the service down

### Changed
- **MIGRATION: amounts are now stored in minor units (cents).** The
  migration multiplies existing price tier and registration amounts
  by 100 — run `db upgrade` exactly once (both update paths do this
  automatically) and spot-check displayed prices afterwards.
- Prices are entered and displayed as dollars everywhere
- Financial admin routes gated by the `financial.manage` permission

### Removed
- nginx deployment path (config, system-level unit, VPS doc) — the
  Cloudflare Tunnel user service is the supported deployment
- Dead payment gateway registry and `PAYMENT_GATEWAY`/`ANZ_*` env vars —
  gateway selection lives in the admin-managed DB config

## [0.1.0] — 2026-05-30

### Added
- Public-facing pages (home, conferences, conference detail, committee, contact, custom Markdown pages)
- OTP-based sign-in system (no passwords stored); email-OTP with rate limiting and attempt lockout
- Member dashboard with conference registration and abstract submission
- Full admin panel: Site Identity, Palette, Fonts, Images, Pages (CMS), Navigation, Footer, Committee, Conferences (with price tiers), Sponsors, Announcements, Members, Permissions matrix, Audit log
- Per-role permissions model (admin, committee, member, reviewer) with 25+ granular permission keys
- CSRF protection on every form; CSP via Talisman; HSTS enforcement
- Upload validation (Pillow, EXIF strip, WebP re-encode, PDF magic check)
- Web-safe font stack system (9 curated stacks, no external font loads)
- Safe Markdown renderer for CMS pages
- Setup wizard for first-run configuration
- Cloudflare Tunnel integration scripts (launch.sh, launch_cloudflared.sh)
- VPS deployment documentation (systemd, nginx)
- Health-check monitor and database backup scripts
- Admin CLI for managing admin users

### Security
- Hardened OTP: single-use, rate-limited, purpose-separated, attempt counter with lockout
- ProductionConfig refuses to start with dev-default SECRET_KEY
- Session hardening: HttpOnly, SameSite=Lax, secure cookies
- Image upload validation with Pillow, EXIF stripping
- CSP: strict, no remote scripts/fonts
- Soft deletes on all user-facing models
- Audit log for all admin/committee actions

### Changed
- Placeholder branding now uses "Name Your Society" (more clearly a template)
- All deployment configuration uses `your-domain.example.org` placeholders
- Site palette variables now have CSS fallbacks in :root

### Fixed
- Removed duplicate Alembic migration
- Fixed dead links in login form (terms of use, code of conduct)
- Fixed missing alt text on images
- Fixed setup wizard hardcoded colors (now uses CSS variables)
- Fixed MAIL_FROM inconsistency between config.py and mail.py
- Fixed launch.sh sed -i macOS compatibility
- Fixed cloudflared-launch.service calling interactive script
- Fixed backup.py pg_dump exposing password on command line

### Removed
- Docker, Caddy, PaaS deployment files (not maintained for V1)
- Unused argon2-cffi dependency
- Vestigial committee column on Conference model
- Dead import of REG_STATUSES and ABSTRACT_STATUSES constants
