# Changelog

## [Unreleased]  **MIGRATION REQUIRED**

### Fixed
- **Editing a registration re-priced it at today's rate.** The fee was derived
  again on every save, so a member who paid the early-bird rate and later came
  back to correct a dietary note — after the early-bird deadline had passed —
  was silently moved to the full rate, billed the difference, and emailed a
  demand for money they could not pay, since the payment link correctly
  reported the registration as settled. The price is now struck when the tier
  is chosen and left alone; a real tier change still re-prices at the current
  rate. What someone was charged is a fact about their registration, not
  something to recompute from the calendar.
- **The payment page quoted the tier price and charged the balance.** The
  checkout has always been minted for what is actually owed, so a part-paid
  registration showed one number and took another. The page now leads with the
  amount due, showing the tier price beside it when the two differ.
- **A manual refund was recorded as zero.** The reversal was booked against the
  outstanding balance, and a paid registration owes nothing — so the society
  handed the money back and the ledger went on saying it had kept it. A
  reversal now restores what was actually received; a settlement still credits
  the balance, which is what stops a status toggled back and forth from
  stacking credits.
- **A payment that succeeded after a cancelled attempt left the registration
  cancelled.** Backing out of the gateway and trying again is ordinary, but the
  capture fell through to the duplicate-payment branch: the registration read
  as cancelled while fully paid, the pay link refused it, and admins were
  emailed about a double payment that had not happened. Reconciliation now
  reaches these registrations too.
- **A webhook that failed verification left no trace.** A bad signature or an
  unparseable body produced a log line inside the gateway module and a 200 on
  the way out — no ledger row, no audit entry. The one webhook most worth
  knowing about is now recorded with its source.
- **Editing a draft abstract destroyed its references.** The code that restores
  saved references had come to sit inside the author-serialisation function, so
  it ran at every keystroke in an author name and once more on submit, each
  time rebuilding the rows from the stored draft and discarding what the author
  had typed — and never restored them on load at all, because the container it
  writes into is not assigned until later in the file. The copy that reached
  the server carried no references, so the body's `[1]` markers matched nothing
  and the submission was refused for a reason the form itself had caused.
  Restored rows now also get the remove button and revalidation that rows added
  by hand have always had.
- **TIFF figures were advertised and refused.** The form offers PNG, JPG, TIFF
  or PDF; the figure handler accepted TIFF and then passed it to an image
  handler that did not, so the author was refused at the moment they pressed
  Submit, by a message listing formats the form had never offered. TIFF — the
  format microscopy arrives in — is now accepted, converted for the booklet,
  and images whose colour mode no encoder will take are reported rather than
  raising a 500.
- **Reaching the abstract limit discarded the abstract.** The per-author limit
  is checked where a draft becomes a submission, and that refusal redirected to
  the dashboard, dropping the whole submission. It now returns the form with
  the work in it and suggests saving a draft.
- **A payment could be credited twice.** One capture can reach the ledger more
  than once — webhooks retry until they are acknowledged, and a single payment
  may arrive as both `payment.paid` and `payment.captured`. Every arrival
  credited the balance again, leaving a registration in credit for money that
  was never received, which a later tier upgrade would then be billed against.
  Deliveries are still all recorded; only the first moves the balance. Marking
  a registration paid by hand now settles what is outstanding rather than the
  full fee, so correcting a status back and forth no longer stacks credits
  either.
- **A registration could be changed while its payment was in the air.** A
  checkout is created for the amount owed at that moment, so a tier changed
  while the payer was on the gateway's page settled a price that no longer
  existed — with the money already taken. Registrations are locked for editing
  while a checkout is open, releasing when the payment lands or the session
  expires. The form says so; the server enforces it, since the page may have
  been open in another tab.
- **Loading a page wrote to the financial ledger.** Reading what a registration
  owed seeded a missing opening balance as a side effect, so merely opening the
  pay page appended a record and committed whatever else was pending. Balances
  are seeded where money is decided; a migration backfills the rows that
  predate charge lines.
- **Payment links are throttled per link rather than per IP.** Behind a proxy
  every visitor shares one address, so the old limit was a single bucket for
  the whole site: ten requests to a nonexistent link locked every genuine payer
  out for an hour. Each link now carries its own budget.
- **A refunded registration could still be paid** through a direct request to
  the member checkout route, which checked only for "paid".
- **Reconciliation now reaches failed registrations**, so a payment that
  succeeded after an earlier failure is recovered when its webhook was missed.
- **Invoice amounts reject unusable input** — negatives and exponent notation
  parsed happily, and a negative price booked a charge that read as credit.
- **The invoice result page no longer indexes the invoice book** — it is rate
  limited like its sibling, and answers unknown references with the same page
  as unpayable ones.
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
- **A large figure crashed the submission instead of being refused.** Pillow
  raises `DecompressionBombError` for images past twice its pixel ceiling, and
  that class is not an `OSError` — so it slipped straight past the handler
  written to catch it and the upload became a 500. Both upload paths now catch
  it and say what is actually wrong: the number of pixels, not the file size,
  so nobody is sent off compressing a file that will fail again at the same
  dimensions. Image inputs also check dimensions in the browser before the
  upload starts.
- **The error page claimed an administrator had been notified. Nobody was.**
  The handler rendered a template and did nothing else. Unhandled errors that
  send a user to the error page now email the site's admins with the time, the
  request, the user and the traceback — and nothing else, since surrounding
  log lines are the likeliest place for an unrelated member's details to
  appear. Repeats of the same failure are suppressed for fifteen minutes so a
  crash in a hot path cannot bury the inbox. The page makes the claim only
  when the email actually went, and the report is sent only from the handler
  that makes it.

### Fixed
- **Previewing an abstract left it unsubmitted, with no way forward.** Preview
  saves the abstract as a draft, and the preview page then offered only Edit
  and a primary-styled "Dashboard" — so an author who previewed their work,
  the careful thing to do, could reasonably believe they had finished. The
  page now says plainly that the abstract has not been submitted, and offers
  **Continue editing**, **Keep as draft**, and **Submit abstract**. Submitting
  from the preview runs the same validation the form does, against the stored
  values, so nothing can get in this way that the form would refuse.

### Added
- **Unsubmitted abstracts are visible to admins.** The abstract list gains a
  **Draft** filter, so an author who is stuck can be found and their draft
  read. Previously drafts appeared only under "All", with no way to isolate
  them.
- **Failed submissions are recorded.** A submission rejected by validation
  used to write nothing anywhere: the author saw red text and the audit log
  showed their login followed by silence, so "it won't let me submit" could
  not be investigated. The attempt and the reason it failed are now logged as
  `abstract.submit_failed`.

### Added
- **Abstract PDFs.** Any abstract can be downloaded as a PDF laid out exactly
  as the booklet will print it — from the admin abstract page, and from a
  presenter's own dashboard. It reuses the booklet's fragment builder rather
  than a second renderer, so a preview cannot drift from the real thing.
- **Submission receipts.** Submitting an abstract now sends the author a
  confirmation with that PDF attached, so formatting can be checked while
  there is still time to change it. Per-conference toggle, on by default; the
  email states plainly that it is a receipt and not a decision. A failed
  render never costs the email, only the attachment. Drafts send nothing.

### Fixed
- **Every entry in the booklet's contents jumped to the same abstract.**
  `\\addcontentsline` records a title and a page number but links to the most
  recent anchor, and an abstract fragment issues no sectioning command — so
  the whole booklet held one anchor and all of the contents entries pointed
  at it. Each abstract now carries its own.
- **Abstracts containing `_`, `%`, `$`, `~` or `^` broke the booklet.** Titles,
  author names and affiliations were escaped for `&`, `#` and `_` only, and
  the body for everything except `_` — so `TiO_2` in a body, or `50%` in a
  title, failed the compile and took the entire conference booklet with it.
  All of it now goes through the one escaping table.

### Known
- The booklet and abstract PDFs set in Latin Modern Roman, not the Helvetica
  the preamble asks for: tectonic runs XeTeX, which accepts `helvet` and
  silently ignores it. `\usepackage[T1]{fontenc}` restores Helvetica but
  triggers a system-wide font scan that is irreproducible and too
  memory-hungry for the capped compile child, so it is deliberately not
  applied. Output is correct, only the typeface differs from the pdflatex era.

### Added
- **External links in the navigation and footer.** The target picker offered
  built-in pages and custom pages only, so a link to anywhere off-site could
  not be created from the admin at all — the `url:` scheme the renderer
  already understood had no way in. Both editors now share one control: pick
  "External link" and a URL box appears. Bare domains are read as https,
  `mailto:` works, and anything that is not http, https or mailto is refused
  with a reason rather than written as a dead link.
- **Removing footer columns and links.** The footer editor had no delete
  control of any kind; a column, once added, was permanent. Both are now
  removable from the editor, and deleting a column takes its links with it.
  Navigation's per-row Delete button worked on the first row whatever you
  clicked — the id travelled in the form, which posts one per row — so it now
  travels in the URL instead.

- **Speaker biographies.** An abstract can carry an optional biography, set
  only by staff who can edit abstracts — speakers cannot write their own from
  the submission form. It appears beside the presenter's portrait on the public
  abstract page whenever it's filled in, and clearing it hides it again.

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
