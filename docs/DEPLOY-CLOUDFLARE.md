# Deploying behind a Cloudflare tunnel

If your host doesn't have a public IP (home server, NAT, mobile, etc.) — a
free Cloudflare account + a domain attached to it is the easiest way to
get TLS + a stable public hostname.

This works as an **overlay** on any of the other deployment styles. The
app keeps listening on `127.0.0.1:5005`; the tunnel proxies traffic into
it. TLS, DDoS shielding, and DNS are all Cloudflare's problem.

## 1. One-time Cloudflare setup

1. Sign up at `https://dash.cloudflare.com` (free tier is enough).
2. Add your domain (transfer or change-nameservers — both work).
3. Install `cloudflared` on the host:

   ```bash
   # https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
   ```

4. Authenticate with your Cloudflare account:

   ```bash
   cloudflared tunnel login
   ```

   That opens a browser; pick the zone you added in step 2. It writes
   `~/.cloudflared/cert.pem`.

## 2. Use the project's launcher

The project ships `scripts/launch_cloudflared.sh` which:

* Creates a tunnel called `society-site` (override with `TUNNEL_NAME`)
* Writes a per-project tunnel config to `./.cloudflared/`
* Launches gunicorn on port 5005
* Points DNS at the tunnel for `app.example.org` (override with
  `SUBDOMAIN` / `DOMAIN`)
* Tears down the gunicorn process when the tunnel exits

```bash
TUNNEL_NAME=my-society SUBDOMAIN=app DOMAIN=example.org ./scripts/launch_cloudflared.sh
```

You'll see a banner with the public URL once everything is up.

## 3. Keeping it running

Wrap the launcher in systemd or a tmux/screen session. A simple unit:

```ini
[Unit]
Description=Society Site + Cloudflare tunnel
After=network-online.target

[Service]
Type=simple
User=society
WorkingDirectory=/opt/society-site
EnvironmentFile=/opt/society-site/.env
ExecStart=/opt/society-site/scripts/launch_cloudflared.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## 4. Multiple sites on one host

Each project keeps its tunnel state in `./.cloudflared/` (not
`~/.cloudflared/`), so two deployments on the same host won't fight over
config files. Just pick a different `TUNNEL_NAME` for each.

## 5. Manual config

If you'd rather drive `cloudflared` yourself, copy
`deploy/cloudflared/config.yml.example` to `.cloudflared/config.yml`,
edit, and run `cloudflared --config .cloudflared/config.yml tunnel run
<name>` from a process supervisor.
