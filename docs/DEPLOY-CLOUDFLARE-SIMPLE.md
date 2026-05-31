# Cloudflare Tunnel deployment — zero-config

Get your society site live in under 5 minutes.  No nginx, no certbot, no
firewall rules, no port forwarding.  Just a domain on Cloudflare and a
scoped API token.

`cloudflared tunnel login` is **never** used — the script calls
Cloudflare's REST API directly via `curl` for tunnel creation, DNS
routing, and ingress configuration.  `cloudflared` is only used for the
final `tunnel run --token` data-plane connection.  If your VPS is ever
compromised, the attacker gets a token scoped to one domain — not full
access to your Cloudflare account.

## What you get

- Automatic TLS (HTTPS) via Cloudflare
- DDoS protection, bot filtering, and CDN caching — all free
- Your app stays on `127.0.0.1` — nothing exposed to the internet except the tunnel
- Works on any Linux host (VPS, Raspberry Pi, old laptop under your desk)

## One-time setup (do this once per host)

### 1. Install dependencies

```bash
# cloudflared — the tunnel connector
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# jq — JSON processor (used for API responses)
sudo apt install jq

# curl, openssl — should already be present; install if not
sudo apt install curl openssl
```

- macOS: `brew install cloudflared jq`
- Other platforms: [cloudflared downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) · [jq downloads](https://jqlang.github.io/jq/download/)

### 2. Point your domain's nameservers to Cloudflare

Change your domain's nameservers at your registrar to Cloudflare's (you
get these when you add the domain to the Cloudflare dashboard).  This is
required so Cloudflare can manage DNS for the tunnel.

### 3. Create a scoped API token

Instead of logging your full Cloudflare account into the VPS, create a
narrowly-scoped API token that can only manage tunnels and DNS for your
domain.

There are two kinds — prefer **account-owned**:

#### Recommended: account-owned (`cfat_` prefix)

1. Go to **Cloudflare Dashboard → Manage Account → Account API Tokens** (not My Profile).
2. Click **Create Token**, then **Create Custom Token**.
3. Give it a name like `conventus-zenboo-org`.
4. Under **Permissions**, add:

   | Row | Scope       | Permission              | Access |
   |-----|-------------|-------------------------|--------|
   | 1   | Account     | Argo Tunnel (Legacy)    | Edit   |
   | 2   | Zone        | DNS                     | Edit   |

5. Under **Zone Resources**: *Include → Specific zone → your-domain.example.org*
6. Under **Account Resources**: *Include → All accounts*
7. Click **Continue to summary**, then **Create Token**.
8. Copy the token (prefix `cfat_`, ~50 characters).
9. **Also copy your Account ID** — find it in the right sidebar of any
   Cloudflare dashboard page (Account Home → "Account ID").  You will
   need this because account-owned tokens cannot enumerate accounts via
   the API.  The launch script will prompt for it.

#### Alternative: user-owned (`cfut_` prefix or legacy)

1. Go to **Cloudflare Dashboard → My Profile → API Tokens**.
2. Same permission rows as above, plus one more:
   - User → User Details : Read
3. User-owned tokens *can* enumerate accounts; you do not need to
   provide your Account ID separately.

#### Both flavours need these exact permissions

| Scope       | Permission              | Access |
|-------------|-------------------------|--------|
| Account     | Argo Tunnel (Legacy)    | Edit   |
| Zone        | DNS                     | Edit   |

The Zone permission must be scoped to your specific domain.

> **The launch script prompts for the token and Account ID automatically**
> on first run — you don't need to edit `.env` by hand.

## Launch

```bash
git clone https://github.com/your-org/conventus.git
cd conventus

# The script handles everything interactively on first run — copies
# .env.example to .env if needed, generates a SECRET_KEY, and prompts
# for domain, API token, Account ID (if needed), and email settings.
chmod +x scripts/launch_cloudflared.sh
./scripts/launch_cloudflared.sh
```

On first run the script walks you through:

1. **Domain & subdomain** — where your site lives
2. **API token** — the scoped token from step 3 above
3. **Account ID** — required for `cfat_` tokens, optional for `cfut_`
4. **Email settings** — SMTP host/port/user/password, or console mode for testing
5. Generate a secure `SECRET_KEY`
6. Install Python dependencies with `uv`
7. Start gunicorn on `127.0.0.1:5005`
8. Create a Cloudflare tunnel via the REST API
9. Push ingress config and set up DNS routing
10. Print the setup URL

First time? Visit `https://yourdomain.com/setup` and paste the one-time
setup password shown in the terminal.

## Keeping it running

### Option A: tmux / screen (quick)

```bash
tmux new -s conventus
./scripts/launch_cloudflared.sh
# Ctrl+B, D to detach
```

### Option B: systemd (permanent, auto-restarts on boot)

```bash
sudo scripts/register-service.sh
```

The script auto-detects the project directory and your username, writes
the unit file with the correct paths, and starts the service.  Nothing to
edit by hand.

## Configuration

All settings live in `.env`:

```env
SECRET_KEY=<auto-generated>
FLASK_ENV=production

# Cloudflare
CLOUDFLARE_DOMAIN=your-domain.example.org     # your domain
CLOUDFLARE_SUBDOMAIN=                         # blank for apex, or e.g. "www"
CLOUDFLARE_API_TOKEN=                         # scoped API token (set by wizard)
CLOUDFLARE_ACCOUNT_ID=                        # only needed for cfat_ tokens

# Performance (optional)
GUNICORN_WORKERS=3
GUNICORN_THREADS=32
PORT=5005
```

## Multiple sites on one host

Each project stores its tunnel state in `.cloudflared/` inside the
project directory — no conflicts.  Clone the repo to another folder,
use a different domain, and run the launcher again.

## Updating

```bash
git pull
uv sync
# If the script is running, Ctrl+C and re-run ./scripts/launch_cloudflared.sh
```
