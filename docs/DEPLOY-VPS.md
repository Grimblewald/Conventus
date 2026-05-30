# Deploying on a single VPS

systemd + nginx + gunicorn. Reliable, ~10-minute setup.

## 1. Provision

A 1 GB / 1 vCPU VPS is enough for SQLite + a few hundred members.
Step up if you expect bursty registration windows; SQLite gets cranky
above ~50 concurrent writers.

```bash
# As root on the host
apt update && apt install -y python3.12 python3.12-venv git nginx
adduser --system --group --home /opt/society-site society
mkdir /var/log/society-site && chown society:society /var/log/society-site
```

## 2. Clone + install

```bash
sudo -u society bash <<'EOF'
cd /opt
git clone <this repo> society-site
cd society-site
pip install uv
uv sync
cp .env.example .env
EOF
```

Edit `/opt/society-site/.env`:

```
SECRET_KEY="(generated)"
FLASK_ENV=production
SESSION_COOKIE_SECURE=1
MAIL_BACKEND=smtp
SMTP_HOST=...
SMTP_USER=...
SMTP_PASS=...
# Optional: Postgres
# DATABASE_URL="postgresql+psycopg://society:pw@localhost:5432/society"
```

## 3. systemd unit

```bash
cp /opt/society-site/deploy/systemd/society-site.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now society-site
journalctl -u society-site -f          # watch the first-run setup password
```

Note the setup password printed to the journal. Don't lose it — if you
do, delete `/opt/society-site/instance/setup-pw` and restart the unit to
generate a new one.

## 4. nginx

```bash
cp /opt/society-site/deploy/nginx/society-site.conf /etc/nginx/sites-available/
# Edit server_name + ssl_certificate paths to match your domain.
ln -s /etc/nginx/sites-available/society-site.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Get a cert with certbot:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.example.org
```

## 5. Open the site

Visit `https://your-domain.example.org/setup` and complete the wizard. Done.

## 6. Promoting more admins

The wizard creates *one* admin. To make more, SSH in and run:

```bash
cd /opt/society-site
sudo -u society uv run python scripts/admin_cli.py
```

## Sizing tips

* Default workers are `3 × 32 = 96` concurrent requests. For 500 simultaneous
  registrants tighten the worker count and bump threads:
  `GUNICORN_WORKERS=4 GUNICORN_THREADS=64`. Watch RAM.
* Move to Postgres if registration spikes regularly produce
  `database is locked` errors in the logs.
* Front the site with Cloudflare (just DNS, no tunnel needed) — its bot
  challenge will swallow most of the abuse traffic before it reaches you.
