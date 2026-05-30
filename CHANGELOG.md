# Changelog

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
