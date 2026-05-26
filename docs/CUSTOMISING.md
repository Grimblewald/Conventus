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
