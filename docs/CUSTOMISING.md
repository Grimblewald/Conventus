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
* **Financial identity** — who issues your documents, in one place:
  legal entity name, ABN, GST registration, address, payment
  instructions, signatory name and role, and an optional letterhead logo
  and signature image. Every document kind draws from this, so your ABN
  and bank details are stated once and cannot disagree between an invoice
  and its receipt. The logo and signature are stored outside the public
  uploads folder and are only viewable by admins with `financial.manage` —
  a signature image should never be publicly reachable.

  The GST setting drives real behaviour in both directions: registered
  gives you "Tax Invoice" titles and a GST/ex-GST breakdown; not
  registered prints an explicit "No GST has been charged" statement and
  never a zero-valued GST line, which would wrongly imply a taxable sale.
* **Invoice/Receipt/Adjustment Note documents** — every payment document is
  a real PDF, compiled from one shared, curated LaTeX skeleton (tectonic,
  no admin-authored raw LaTeX — see [SECURITY.md](SECURITY.md#document-rendering)),
  attached to a plaintext email. Each kind has its own editor under
  `/admin/financial/documents/<kind>` and says what it should: an
  **invoice** requests payment (amount due, due date, pay-online link and
  bank details), a **receipt** confirms payment received (total received,
  amount owing nil, date paid), and an **adjustment note** records a
  refund or correction. Each editor has:
  - **Email cover** — subject, plaintext body, from name/address, footer.
    The formal document is the attached PDF, so the email stays short.
  - **PDF body** — optional free text printed below the totals. The
    letterhead, line items, tax lines and sign-off are built for you.

  The full variable vocabulary — `{user_name} {user_email}
  {conference_title} {conference_dates} {tier_name} {amount} {gst_amount}
  {amount_ex_gst} {currency_code} {currency_symbol} {transaction_id}
  {payment_date} {due_date} {site_name} {registration_id} {invoice_type}
  {business_legal_name} {business_number} {business_address}
  {business_contact_email} {signatory_name} {signatory_role}
  {recipient_abn} {recipient_address} {payment_instructions}
  {payment_link} {payment_reference} {sanitized_invoice_ref}` — is listed on both the dashboard and each template
  editor. **Download preview** renders the current form (including unsaved
  edits) as a PDF with every unset variable shown as its **bold field
  name** — e.g. `{amount}` prints as **amount** — rather than a guessed or
  zeroed value, so it's obvious at a glance what's real data vs. a
  placeholder. Preview never records anything (no ledger row, no email
  sent); a warmed cache serves instantly when the saved template is
  unchanged, otherwise it recompiles on the spot.

  The Financial dashboard also shows a **PDF documents** status line —
  green when tectonic is installed and ready, a loud warning (pointing at
  `scripts/install-tectonic.sh`) if it's missing, since there is no
  fallback: without tectonic, no invoice/receipt/adjustment note can be
  generated at all.
* **Send Invoice** — manually bill arbitrary recipients (e.g. sponsors)
  with To/CC, amount, reference, optional recipient address and ABN, and a
  per-invoice GST toggle. **Download preview** renders the exact PDF the
  recipient will receive — same values, same pay link — without emailing
  or recording anything, so you can check it before it goes out. Sending
  attaches the PDF and embeds a durable pay-online link; paying it sends
  the receipt automatically.
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
