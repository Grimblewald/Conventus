# Keeping a deployment up to date

The project is designed to be updated with `git pull`. Schema changes are
introduced as additive migrations whenever possible so existing deployments
keep working without manual intervention.

## Standard update procedure

```bash
cd /opt/society-site                 # or wherever you cloned
sudo -u society git pull
sudo -u society uv sync               # picks up new/removed dependencies
sudo systemctl restart society-site   # or `docker compose up -d app`
```

That's it for typical changes — CSS tweaks, new admin panels, copy
edits, additive columns.

## When a release has a migration

Look in the release notes (or `CHANGELOG.md`) for a line marked
**MIGRATION REQUIRED**. The standard recipe:

```bash
sudo -u society uv run flask --app wsgi:app db upgrade
sudo systemctl restart society-site
```

The very first time you run this on an older deployment that doesn't have
a migrations table yet, stamp the current revision first:

```bash
sudo -u society uv run flask --app wsgi:app db stamp head
```

## Update notifier (future)

The admin panel exposes `app/services/updater.py` which can read your git
remote (set `UPDATE_REMOTE_URL` in `.env`) and tell admins when a newer
commit is available. The current implementation only shows a status
message — emailing a digest to admins when updates land is a planned
follow-up. The current stub is small enough that you can call its
`latest_status()` from a cron job today if you want to script your own
notifier.

## Rolling back

* SQLite — `cp instance/app.db.backup-YYYYMMDD instance/app.db`, restart.
* Postgres — restore from `pg_dump`, then check out the previous git tag
  and `flask db downgrade` if a migration ran.
* Always keep uploads + DB snapshots aligned in time so a rollback isn't
  pointing at images that disappeared.

## Watching breaking changes

Every release that drops a column, renames a route, or removes a
permission key will be tagged `BREAKING` in the changelog. The migrations
that ship with such releases will include a comment about what to do
about it. For minor releases, just pull and restart.
