# Deploying with Docker / docker-compose

`deploy/docker-compose.yml` brings up the app, Postgres, Redis, and Caddy
(which handles TLS automatically via Let's Encrypt).

## 1. Configure

```bash
cp .env.example .env
$EDITOR .env
# Also set POSTGRES_PASSWORD in .env — the compose file references it.
```

Edit `deploy/Caddyfile` and replace `example.org` with your domain.

## 2. Build + run

```bash
cd <repo>
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f app   # watch for setup-pw
```

## 3. First-run setup

Visit `https://your-domain.example.org/setup`. Paste the password printed
to the app log. The setup file lives at
`./instance/setup-pw` inside the bind-mounted volume.

## 4. Updating

```bash
git pull
docker compose -f deploy/docker-compose.yml build app
docker compose -f deploy/docker-compose.yml up -d app
```

Read [`UPDATING.md`](UPDATING.md) before pulling — non-trivial migrations
are flagged in changelog notes.

## Sizing for bursty load

The compose file uses `redis:` for rate limit storage so multiple gunicorn
workers share OTP throttles correctly. To run more workers, set the env
vars in `.env`:

```
GUNICORN_WORKERS=6
GUNICORN_THREADS=32
```

`docker compose up -d app` reloads them.
