# Customising the site

Every customisable surface lives under `/admin`. Permissions are toggled
per-role — admin always has everything, every other role starts with
nothing until you grant.

## Site → Identity

`/admin/site/identity` — the name on the masthead, the browser tab title,
the contact email, the copyright line, the currency code + symbol used on
conference registration. Locale + timezone are accepted but currently
informational only.

## Site → Palette

`/admin/site/palette` — every colour on the page is a CSS variable backed
by a DB column. Pick a swatch and the live preview updates immediately;
Save persists. The fields are:

* Page background / text / muted text
* Header background / text
* Footer background / text
* Link / link hover
* Accent / accent text (used on tags, status pills, focus rings)
* Card background / border
* Button background / text

We store either hex (`#abcdef`), `rgb()`, `hsl()`, `oklch()` — anything
CSS accepts. The validator on the server rejects garbage so a bad value
can't break rendering.

## Site → Fonts

Four picks — heading, body, link, UI — from a curated list of web-safe
system stacks (Georgia, Helvetica/system, Tahoma, Palatino, Avenir,
Lucida, Rockwell, monospace). **No external fonts are ever loaded**, which
is why the CSP can stay tight.

## Site → Images

Four slots:

| Slot       | Used as                              | Target size |
| ---------- | ------------------------------------ | ----------- |
| Logo       | Header / footer wordmark            | width 800px |
| Favicon    | Browser tab + `/favicon.ico`         | 256 × 256   |
| Hero       | Home-page hero image                 | width 1920  |
| OG image   | Social-share preview thumbnail       | width 1200  |

Uploads are Pillow-validated, re-encoded as WebP/PNG, and stored under
`uploads/site/`. There is no editor for raw image bytes — uploads are
always re-encoded as a defense against malicious files.

## Pages

`/admin/pages` — Markdown-bodied pages reachable at `/p/<slug>`. The
wizard seeds `about`, `privacy`, `terms`, `code-of-conduct` for you;
those slugs are stable, but you can change the title/body of any of them,
or add as many new pages as you want. Soft-deletes are recoverable from
the audit log if you need them.

## Navigation

`/admin/nav` — the top-bar links. Each item picks a `target`:
- built-in (`home`, `conferences`, `committee`, `contact`)
- a `page:<slug>` reference to a custom page
- an external `url:...`

Items are visible-toggleable and orderable with the integer order column
(lower = earlier).

## Footer

`/admin/footer` — same target scheme as Nav. Footer has any number of
columns, each with any number of links. The wordmark column in the
footer is rendered automatically from the Site → Identity values.

## Committee

`/admin/committee` — full CRUD over committee profiles. Portraits are
square-cropped automatically on upload. Optional link to a User row
unlocks self-edit (give that user the `committee.edit_self` permission
and they can change their own profile from the same panel).

## Conferences

`/admin/conferences` — list + create. Each conference has its own edit
page with everything else: hero, booklet PDF, price tiers (one row per
tier, with name + amount), tracks, deadlines, featured flag, draft flag.
Deletion goes through an emailed OTP confirmation.

## Announcements

`/admin/announcements` — News / Call / Award / Event tagged posts shown
on the home page. Pinned items always appear first.

## Members

`/admin/users` — list / filter by role / soft-delete / role change.
Admins can't be edited here — promotions happen via
`scripts/admin_cli.py`.

## Permissions

`/admin/permissions` — pick a role from the dropdown (Unregistered,
Member, Committee) and tick which permissions it gets. Every gated route
in the codebase declares a key like `conf.create` — the same keys appear
here, grouped by section. Admin is implicit and not in the list.

## Audit log

`/admin/audit` — append-only history of every admin/committee mutation.
Filter by action substring or actor email.

## Financial

`/admin/financial` — the whole payment lifecycle, gated on the
`financial.manage` permission (grantable to committee; implies
`registrations.view`):

* **Payment Providers** — ANZ Worldline credentials (API + webhook
  key/secret, stored encrypted), Test Connection, and the OTP-confirmed
  Sandbox/Live toggle. The gateway always (re-)enables into sandbox; live
  is only reachable via the OTP flow. A status line spells out whether
  member payments are open and offers the explicit Open/Close switch —
  members only ever reach checkout when the gateway is enabled, live,
  *and* opened. While closed, admins and financial managers can still run
  test payments on their own registrations.
* **Testing** — send the invoice template (rendered with sample data) to
  any address, and run a small test payment (≤ $10) not tied to any
  registration; settlement is confirmed by webhook → admin email + audit.
* **Invoice Template** — subject, plain/HTML body, from name/address, and
  footer for the emails sent automatically when a payment or refund
  settles. Variables like `{user_name}`, `{amount}`, `{transaction_id}`
  are listed on the dashboard.
* **Send Invoice** — manually bill arbitrary recipients (e.g. sponsors)
  with To/CC, amount, and reference; uses the same template (review its
  wording — the default reads as a receipt).
* **Transactions** — searchable per-transaction ledger of every checkout
  created, gateway webhook event (with the status change it caused), and
  manual invoice sent.

Registration payment webhooks (`/payments/webhook`) are verified by HMAC
signature; refunds, disputes, and suspected double payments email all
admins and appear in the audit log as `financial.payment_attention`.

### Understanding transaction IDs

The gateway platform (Worldline Global Online Pay) identifies things at
two levels, and mixing them up makes reconciliation look broken:

* **Merchant reference** — *our* identifier for the whole payment
  lifecycle: `reg_<id>` for registrations, `test_<token>` for admin test
  payments, `INV-…` for sent invoices. Every operation on a payment
  (authorisation, capture, refund, void) carries the same reference. The
  Transactions ledger groups by it, and the Merchant Portal's transaction
  search uses it too (with `%` wildcards). **This is the join key**
  between our ledger, the portal, and any treasurer notes.
* **Payment/operation ID** — the long number the platform generates
  (e.g. `9000009599513317000`). Each *operation* gets its own: observed
  in practice, an authorisation and its later void share all digits
  except a trailing counter (`…000` → `…001`), i.e. the number embeds
  the payment's identity plus a per-operation history index — the modern
  form of the legacy platform's PAYID + PAYIDSUB pair. The exact digit
  layout is not publicly documented, so treat the full string as opaque:
  match payments by merchant reference, and read a differing tail as
  "another operation on the same payment", not a new payment.

In the Merchant Portal, operations do **not** appear as separate rows:
find the transaction by merchant reference, open it, and the **History**
section lists each authorisation/capture/refund/void with its result.
A voided authorisation never settles, so it shows no settled amount and
disappears from bank statements when the hold lapses. Settlement-side
numbers (RRN/ARN in acquirer reports and on cardholder statements) come
from the card schemes and will never match platform payment IDs —
reconcile those against settlement reports by amount, date, and merchant
reference, not by ID.
