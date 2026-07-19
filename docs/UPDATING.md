# Keeping a deployment up to date

There are two supported update paths. Both back up first, pull the latest
code, apply database migrations, and restart the service — you never run
migrations by hand in normal operation.

## Path 1 — from the admin panel (recommended)

**Admin → System → Update.** The page shows whether a newer commit is
available (set `UPDATE_REMOTE_URL` in `.env` to enable the check). Clicking
Update emails you a confirmation code; entering it runs `git pull`, applies
migrations, and queues a service restart. You land on a "site is
restarting" page that polls every couple of seconds and returns you to the
admin overview when the site is back — usually well under a minute.

Every update is audit-logged as `site.updated`.

## Path 2 — from the shell

```bash
cd ~/Conventus            # or wherever you cloned
uv run python -m app update
```

This backs up the database to `var/backups/`, pulls, migrates, and
restarts the `cloudflared-launch` user service. If the update goes
sideways:

```bash
uv run python -m app revert
```

restores the exact pre-update state (database + git ref) in one step.

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

* `uv run python -m app revert` undoes the most recent `update` exactly.
* For anything older, restore a backup zip: **Admin → System → Backup**
  (OTP-gated, takes a safety backup first) or
  `uv run python scripts/backup.py --restore <zip>`. Backup archives
  record the git commit and migration head they were taken at
  (`manifest.json`), so check out that commit before restoring a backup
  made on older code.
* Keep uploads + DB snapshots aligned in time — the backup zips bundle
  both precisely so a rollback isn't pointing at images that disappeared.
