# Changelog

## [Unreleased]

### Fixed
- **The abstract booklet PDF could not be compiled.** It still shelled out to
  `pdflatex`, which no deploy installs — the site moved to tectonic for its
  documents and the booklet was left behind, so the button only ever reported
  "pdflatex is not installed on this server". The booklet now compiles through
  the same renderer as invoices and receipts, which also puts it behind the
  shared compile queue and box-wide lock (a long booklet can't starve invoice
  rendering), runs it without shell-escape and with the project root no longer
  its working directory, and caps its memory. `pdflatex` is gone from the
  codebase; tectonic is the one toolchain.

### Fixed
- **Document rendering could take the site down.** The renderer assumed a
  single-process deploy; gunicorn runs several workers, each of which warmed
  the PDF cache at boot. The concurrent compiles exhausted memory on a small
  host and the OOM killer took the web server with them. Boot warming is now
  opt-in (`DOC_WARM_ON_BOOT`, default off), every compile takes a box-wide
  lock so only one runs per machine, and the compile is capped to a share of
  physical RAM sized to the host — this project targets a Pi, not a server.
- Static assets are served with a content hash, so a deploy can no longer
  strand returning visitors on stale cached JavaScript or CSS.
- The tectonic installer falls back to the static musl build when the
  official binary cannot run (Ubuntu 22.04+ no longer ships libssl 1.1).

### Changed
- **Invoices no longer read like receipts.** The invoice template carried the
  old single-template wording and told recipients their payment had been
  received. Each kind now ships wording that matches what it does; the
  migration only replaces text a society has not edited.
- **One Financial identity** — legal entity, ABN, GST registration, address,
  payment instructions, signatory and letterhead now live in one place shared
  by every document kind, instead of being restated per template. Signature
  and logo images are stored outside the public uploads tree.
- **Documents look like real financial documents** — letterhead, bill-to
  block, ruled line-item table, and a totals stack that ends on amount due,
  amount owing, or amount adjusted according to kind. A society that is not
  registered for GST now says so explicitly instead of leaving it unstated.

### Added
- Editors for the receipt and adjustment-note templates, which previously had
  no admin UI at all.
- **Download preview** on the Send Invoice form, rendering the actual invoice
  to be sent rather than a blank template.

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
