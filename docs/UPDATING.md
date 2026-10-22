# Keeping a deployment up to date

Both ways of updating run the same code, `scripts/site_update.py`: write a
restore point (the database, copied safely while the site runs, and the git
commit), pull the new version, install dependencies, apply database
migrations, restart. You never run migrations by hand in normal operation.
Restore points live in `var/backups/`; they are never overwritten and the
newest ten are kept.

## From the admin panel

**Admin → System → Update.** The page shows whether a newer commit is
available (set `UPDATE_REMOTE_URL` in `.env` to enable the check). Clicking
Update emails you a confirmation code; entering it runs the update and queues
a service restart. You land on a "site is restarting" page that polls every
couple of seconds and returns you to the admin overview when the site is back
— usually well under a minute. If a step fails, nothing after it runs and the
page shows the output. Every update is audit-logged as `site.updated`.

## From the shell

```bash
cd ~/Conventus            # or wherever you cloned
scripts/update.sh         # or: uv run python -m app update
```

## If the site does not come back

SSH in and run:

```bash
scripts/update.sh --revert
```

It stops the site, keeps the current database as a `*-pre-revert` copy (so
the revert can itself be undone), puts back the newest restore point's
database and code, and starts the site. It asks first; `--yes` skips the
question. Use this rather than `python -m app revert` when the site is
broken: the script uses only Python's standard library and never imports the
application, so it works even when the new code will not start.

## Migrations

Schema changes ship as Alembic migrations and are applied automatically by
both update paths. Releases whose migrations need extra care (data
conversions, anything not purely additive) are marked **MIGRATION
REQUIRED** in `CHANGELOG.md` — read the entry before updating, and take a
backup from **Admin → System → Backup** first.

If you ever need to run migrations manually:

```bash
uv run flask --app wsgi:app db upgrade
```

On a very old deployment without a migrations table, stamp once first:

```bash
uv run flask --app wsgi:app db stamp head
```

## Rolling back

* `scripts/update.sh --revert` rolls back to the most recent restore point
  (see above). `scripts/update.sh list` shows them all, and
  `python3 scripts/site_update.py revert --to var/backups/<dir>` puts back a
  specific one.
* For anything older, restore a backup zip: **Admin → System → Backup**
  (OTP-gated, takes a safety backup first) or
  `uv run python scripts/backup.py --restore <zip>`. Backup archives
  record the git commit and migration head they were taken at
  (`manifest.json`), so check out that commit before restoring a backup
  made on older code.
* Keep uploads + DB snapshots aligned in time — the backup zips bundle
  both precisely so a rollback isn't pointing at images that disappeared.
