#!/usr/bin/env bash
# End-to-end launcher: gunicorn + Cloudflare tunnel.
#
# Every piece of tunnel state — origin cert, per-tunnel credentials,
# ingress config, pidfile and logs — lives inside the project under
# .cloudflared/ and var/. We never touch ~/.cloudflared and never edit
# the user's global cloudflared config. That's the whole point: two
# different projects on the same host must not fight over a single
# global config file or a single global credentials cache.
#
# Edit the four variables below, or set them as env vars before invoking.
set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-society-site}"
SUBDOMAIN="${SUBDOMAIN:-app}"
DOMAIN="${DOMAIN:-your-domain.example.org}"
PORT="${PORT:-5005}"
PROJECT="${PROJECT:-Society Site}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/.cloudflared"
RUN_DIR="$PROJECT_ROOT/var"
CONFIG_FILE="$CONFIG_DIR/config.yml"
ORIGIN_CERT="$CONFIG_DIR/cert.pem"
TUNNEL_CREDS="$CONFIG_DIR/$TUNNEL_NAME.json"
FLASK_LOG="$RUN_DIR/flask.log"
TUNNEL_LOG="$RUN_DIR/cloudflared.log"
FLASK_PIDFILE="$RUN_DIR/flask.pid"

mkdir -p "$CONFIG_DIR" "$RUN_DIR"

# Refuse to run if the per-project cert.pem isn't permissioned tightly.
# cloudflared cert.pem is sensitive: anyone who can read it can mint
# tunnels against your Cloudflare account.
chmod 700 "$CONFIG_DIR" 2>/dev/null || true

# All cloudflared invocations go through this wrapper so they only ever
# read/write inside CONFIG_DIR. --origincert is the one knob that
# controls where cloudflared looks for cert.pem.
cfd() { cloudflared --origincert "$ORIGIN_CERT" "$@"; }

echo "→ $PROJECT (tunnel: $TUNNEL_NAME → $SUBDOMAIN.$DOMAIN, app: :$PORT)"
echo "  state dir: $CONFIG_DIR"

command -v cloudflared >/dev/null || {
    echo "✗ cloudflared not installed — see https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" >&2
    exit 1
}

# Bootstrap the origin cert by COPYING (not symlinking) it once from
# ~/.cloudflared/cert.pem if it's there. After that the project is
# self-contained — wipe ~/.cloudflared and this script keeps working.
if [ ! -f "$ORIGIN_CERT" ]; then
    if [ -f "$HOME/.cloudflared/cert.pem" ]; then
        cp "$HOME/.cloudflared/cert.pem" "$ORIGIN_CERT"
        chmod 600 "$ORIGIN_CERT"
        echo "  copied origin cert from ~/.cloudflared/cert.pem (one-time)"
    else
        echo "✗ no cert.pem in $CONFIG_DIR or ~/.cloudflared" >&2
        echo "  run \`cloudflared tunnel login\`, then re-run this script." >&2
        exit 1
    fi
fi

# Create the tunnel if it doesn't exist, putting its credentials JSON
# inside the project dir. --credentials-file is what stops cloudflared
# from defaulting to ~/.cloudflared/<UUID>.json.
if ! cfd tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
    echo "  creating tunnel $TUNNEL_NAME (credentials → $TUNNEL_CREDS)"
    cfd tunnel create --credentials-file "$TUNNEL_CREDS" "$TUNNEL_NAME"
fi

if [ ! -f "$TUNNEL_CREDS" ]; then
    echo "✗ expected tunnel credentials at $TUNNEL_CREDS but none found." >&2
    echo "  the tunnel was probably created against ~/.cloudflared earlier." >&2
    echo "  either move its JSON in, or \`cfd tunnel delete $TUNNEL_NAME\` and rerun." >&2
    exit 1
fi
chmod 600 "$TUNNEL_CREDS"

# Project-local ingress config. We will pass --config explicitly when
# running the tunnel so cloudflared never falls back to its global one.
cat > "$CONFIG_FILE" << EOF
tunnel: $TUNNEL_NAME
credentials-file: $TUNNEL_CREDS
origincert: $ORIGIN_CERT
ingress:
  - hostname: $SUBDOMAIN.$DOMAIN
    service: http://localhost:$PORT
  - service: http_status:503
EOF

# --- launch gunicorn in the background -----------------------------------
"$PROJECT_ROOT/scripts/launch.sh" > "$FLASK_LOG" 2>&1 &
FLASK_PID=$!
echo $FLASK_PID > "$FLASK_PIDFILE"

cleanup() {
    if [ -f "$FLASK_PIDFILE" ]; then
        kill "$(cat "$FLASK_PIDFILE")" 2>/dev/null || true
        rm -f "$FLASK_PIDFILE"
    fi
}
trap cleanup EXIT INT TERM

sleep 2
if ! kill -0 "$FLASK_PID" 2>/dev/null; then
    echo "✗ gunicorn failed to start — see $FLASK_LOG" >&2
    exit 1
fi

# --- DNS route ------------------------------------------------------------
TUNNEL_ID=$(cfd tunnel list | awk -v n="$TUNNEL_NAME" '$2==n {print $1}')
cfd tunnel route dns --overwrite-dns "$TUNNEL_ID" "$SUBDOMAIN.$DOMAIN" || true

echo
echo "  Public:  https://$SUBDOMAIN.$DOMAIN"
echo "  Local:   http://localhost:$PORT"
echo "  Logs:    $FLASK_LOG / $TUNNEL_LOG"
echo "  Ctrl+C to stop."
echo

# --config points at the project-local ingress; cloudflared will NOT
# read ~/.cloudflared/config.yml.
exec cfd --config "$CONFIG_FILE" tunnel run "$TUNNEL_NAME"
