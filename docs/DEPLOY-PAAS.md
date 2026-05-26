# Deploying on a PaaS

The codebase is generic enough to drop onto Fly.io, Render, Railway, or any
other managed platform that runs a Python web app.

What every PaaS needs from you:

| Question                  | Answer                              |
| ------------------------- | ----------------------------------- |
| Build command             | `pip install uv && uv sync`         |
| Start command             | `gunicorn -b 0.0.0.0:$PORT wsgi:app` |
| Python version            | 3.12                                |
| Persistent disk           | mount at `/app/instance` and `/app/uploads` |
| Required env vars         | `SECRET_KEY`, `DATABASE_URL`, plus SMTP if you want real email |

Most providers translate "persistent disk" differently — Fly uses
Volumes, Render uses Disks, Railway calls them Volumes. The two paths to
persist are:

* `instance/` — SQLite DB (if you didn't set DATABASE_URL), the
  `.setup-complete` flag, and the wizard's one-time password file.
* `uploads/` — committee portraits, hero images, abstract figures.

If you're using a managed Postgres add-on you only need to persist
`uploads/` (the `instance/` directory still holds the setup flag — if you
drop it you'll be back at the wizard, which is fine as long as the DB
is intact).

## Recommended: external object storage

If your PaaS has S3-compatible object storage, you can substitute that for
`uploads/`. The code reads `UPLOAD_FOLDER` so pointing it at an FUSE-mounted
bucket is the simplest path. A first-class S3 backend isn't implemented
yet — flagged in `docs/UPDATING.md` as a future feature.

## Setup password on read-only platforms

A few PaaSes start with a read-only filesystem until you mount a disk.
The wizard will fail to write `setup-pw` in that case. Either:

* Pre-create the volume + mount it at `/app/instance` BEFORE first boot, or
* Set `SETUP_PASSWORD_PATH` to a writable temp directory and read the
  password from logs.

## OTP throttle storage

PaaS deployments scale horizontally — instances come and go. To make
OTP rate limits stick across replicas, attach a Redis add-on and set:

```
RATELIMIT_STORAGE_URI=redis://...
```

Otherwise each replica enforces limits independently and an attacker can
parallelise to defeat them.
