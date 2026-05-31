#!/usr/bin/env bash
# launch_cloudflared.sh — Conventus all-in-one launcher.
#
# Brings up gunicorn + a Cloudflare Tunnel using ONLY a scoped API
# token. `cloudflared tunnel login` is never used — tunnel creation,
# DNS routing and ingress config all go through the REST API.
# cloudflared only handles the data plane via `tunnel run --token`.
#
# All state (token, ids, logs, pidfile) lives under the project in
# .cloudflared/ and var/. We never touch ~/.cloudflared and never
# edit the user's global cloudflared config.
#
# API token — two flavours:
#
#   Account-owned (recommended — prefix `cfat_`):
#     Dashboard → Manage Account → Account API Tokens → Create Token
#     Bound to a single account; survives the creator leaving.
#     Cannot enumerate accounts on its own — set CLOUDFLARE_ACCOUNT_ID
#     in .env (find it in the sidebar of any Cloudflare dashboard page).
#
#   User-owned (prefix `cfut_` or legacy unprefixed):
#     Dashboard → My Profile → API Tokens → Create Token
#     Can enumerate accounts via the API.  Same scopes below, plus
#     User → User Details : Read.
#
# Required scopes for either flavour:
#     Account → Argo Tunnel (Legacy) : Edit
#     Zone    → DNS                 : Edit  (limit to your domain)
#
# A compromised VPS leaks only this scoped token, never your full
# Cloudflare account login.

set -euo pipefail

# ─── paths & defaults ─────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/.cloudflared"
RUN_DIR="$PROJECT_ROOT/var"
STATE_TOKEN="$CONFIG_DIR/connector-token"
STATE_TUNNEL_ID="$CONFIG_DIR/tunnel-id"
STATE_ACCOUNT_ID="$CONFIG_DIR/account-id"
STATE_ZONE_ID="$CONFIG_DIR/zone-id"
FLASK_LOG="$RUN_DIR/flask.log"
FLASK_PIDFILE="$RUN_DIR/flask.pid"
ENV_FILE="$PROJECT_ROOT/.env"

TUNNEL_NAME="${TUNNEL_NAME:-conventus}"
PORT="${PORT:-5005}"

mkdir -p "$CONFIG_DIR" "$RUN_DIR"
chmod 700 "$CONFIG_DIR" "$RUN_DIR" 2>/dev/null || true

echo "→ Conventus launcher starting..."
echo "  project root: $PROJECT_ROOT"

# ─── .env helpers ─────────────────────────────────────────────────────
_strip_quotes() {
    local val="$1"
    val="${val#\"}"; val="${val%\"}"
    val="${val#\'}"; val="${val%\'}"
    printf '%s' "$val"
}

_quote_env_val() {
    local v
    v="$(_strip_quotes "$1")"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    v="${v//\$/\\\$}"
    v="${v//\`/\\\`}"
    printf '"%s"' "$v"
}

_set_env() {
    local key="$1" raw="$2" tmp="$ENV_FILE.tmp" val
    val="$(_quote_env_val "$raw")"
    if grep -qE "^#?[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null; then
        K="$key" V="$val" awk '
            BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"]; done = 0 }
            {
                if (!done && match($0, "^#?[[:space:]]*" k "=")) {
                    print k "=" v
                    done = 1
                } else {
                    print
                }
            }
        ' "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
    else
        cp "$ENV_FILE" "$tmp"
        printf '%s=%s\n' "$key" "$val" >> "$tmp"
        mv "$tmp" "$ENV_FILE"
    fi
}

_get_env() {
    local raw
    raw="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    _strip_quotes "$raw"
}

# ─── interactive first-run wizard ─────────────────────────────────────
_wizard() {
    [ -t 0 ] || return 0
    echo "→ Checking configuration..."

    if [ ! -f "$ENV_FILE" ]; then
        echo "  .env not found — copying from .env.example"
        cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    fi

    local cur_domain cur_token cur_secret
    cur_domain="$(_get_env CLOUDFLARE_DOMAIN)"
    cur_token="$(_get_env CLOUDFLARE_API_TOKEN)"
    cur_secret="$(_get_env SECRET_KEY)"

    local placeholder_domain="your-domain.example.org"
    local placeholder_secret="CHANGE-ME-generate-with-secrets.token_urlsafe-48"

    local need=0
    if [ -z "$cur_domain" ] || [ "$cur_domain" = "$placeholder_domain" ]; then need=1; fi
    if [ -z "$cur_token" ]; then need=1; fi
    if [ -z "$cur_secret" ] || [ "$cur_secret" = "$placeholder_secret" ] || [[ "$cur_secret" == CHANGE-ME* ]]; then need=1; fi
    if [ "$need" = 0 ]; then
        echo "  All core settings present — nothing to configure."
        return 0
    fi

    cat << 'INTRO'

┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  Conventus — first-run setup                                     │
│                                                                  │
│  Let's configure the essentials so everything works out of the   │
│  box.  Advanced settings use sensible defaults — you can edit    │
│  .env later to tweak them.                                       │
│                                                                  │
│  Press Enter to accept the [default] shown in brackets.          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
INTRO

    # SECRET_KEY
    if [ -z "$cur_secret" ] || [ "$cur_secret" = "$placeholder_secret" ] || [[ "$cur_secret" == CHANGE-ME* ]]; then
        local new_secret
        new_secret="$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
                   || python  -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
                   || openssl rand -base64 36 2>/dev/null)"
        if [ -n "$new_secret" ]; then
            _set_env "SECRET_KEY" "$new_secret"
            echo "  SECRET_KEY = (generated)"
        fi
    fi

    # Domain
    local ans
    if [ -z "$cur_domain" ] || [ "$cur_domain" = "$placeholder_domain" ]; then
        printf '\nDomain your site will be served on\n  (e.g. your-society.example.org): '
        read -r ans </dev/tty || ans=""
        [ -n "$ans" ] && _set_env "CLOUDFLARE_DOMAIN" "$ans"
    fi

    # Subdomain
    printf 'Subdomain [app] (blank for apex, e.g. "www"): '
    read -r ans </dev/tty || ans=""
    [ -n "$ans" ] && _set_env "CLOUDFLARE_SUBDOMAIN" "$ans"

    # API token
    cur_token="$(_get_env CLOUDFLARE_API_TOKEN)"
    if [ -z "$cur_token" ]; then
        cat << 'TOKEN'

  A Cloudflare API token lets this script create your tunnel and
  set up DNS without giving the VPS full account access.  No
  `cloudflared tunnel login` needed — everything runs over the
  Cloudflare REST API.

  RECOMMENDED — account-owned token (prefix `cfat_`):
    Dashboard → Manage Account → Account API Tokens → Create Token
    Scopes:
      Account → Argo Tunnel (Legacy) : Edit
      Zone    → DNS                 : Edit  (limit to your domain)
    Also paste your Account ID (from the right sidebar of any
    Cloudflare page) when asked — account-owned tokens cannot
    enumerate accounts on their own.

  Alternative — user-owned token (prefix `cfut_` or legacy):
    Dashboard → My Profile → API Tokens → Create Token
    Same two scopes, plus  User → User Details : Read.

  Full walkthrough: docs/DEPLOY-CLOUDFLARE-SIMPLE.md

TOKEN
        printf 'Cloudflare API token: '
        read -r ans </dev/tty || ans=""
        [ -n "$ans" ] && _set_env "CLOUDFLARE_API_TOKEN" "$ans"
    fi

    # Account ID (needed for account-owned / cfat_ tokens)
    local cur_acct token_now
    cur_acct="$(_get_env CLOUDFLARE_ACCOUNT_ID)"
    token_now="$(_get_env CLOUDFLARE_API_TOKEN)"
    if [ -z "$cur_acct" ] && [[ "$token_now" == cfat_* ]]; then
        cat << 'ACCT'

  Account-owned tokens are bound to a single account.  Paste your
  Account ID — find it in the Cloudflare dashboard's right sidebar
  on any page (Account Home → "Account ID").  32 hex characters.

ACCT
        printf 'Cloudflare Account ID: '
        read -r ans </dev/tty || ans=""
        [ -n "$ans" ] && _set_env "CLOUDFLARE_ACCOUNT_ID" "$ans"
    fi

    # Mail
    local current_domain val
    current_domain="$(_get_env CLOUDFLARE_DOMAIN)"
    echo ""
    echo "  Email delivery:"
    echo "    1. smtp    — send real emails (OTP login codes, contact form, alerts)"
    echo "    2. console — print to terminal (testing only — no real email delivery)"
    printf '  Choose [1/2, default: 1]: '
    read -r ans </dev/tty || ans=""
    ans="${ans:-1}"

    if [ "$ans" = "1" ]; then
        _set_env "MAIL_BACKEND" "smtp"

        printf '  SMTP server hostname (e.g. smtp.gmail.com): '
        read -r val </dev/tty || val=""
        [ -n "$val" ] && _set_env "SMTP_HOST" "$val"

        printf '  SMTP port [587]: '
        read -r val </dev/tty || val=""
        _set_env "SMTP_PORT" "${val:-587}"

        printf '  SMTP username: '
        read -r val </dev/tty || val=""
        [ -n "$val" ] && _set_env "SMTP_USER" "$val"

        printf '  SMTP password: '
        stty -echo 2>/dev/null || true
        read -r val </dev/tty || val=""
        stty echo 2>/dev/null || true
        echo ""
        [ -n "$val" ] && _set_env "SMTP_PASS" "$val"

        local default_from="Name Your Society <noreply@${current_domain}>"
        printf '  From address ["%s"]: ' "$default_from"
        read -r val </dev/tty || val=""
        _set_env "MAIL_FROM" "${val:-$default_from}"
    else
        _set_env "MAIL_BACKEND" "console"
        echo "  Email set to console mode (no real delivery)."
    fi

    cat << 'OUTRO'

  ✔  Settings saved to .env

  Note: rate limiting uses in-process memory by default, which is
  fine for single-worker or small deployments.  If you scale to
  many gunicorn workers, set RATELIMIT_STORAGE_URI=redis://... in
  .env to share limits across workers.

OUTRO
}

_wizard

# ─── load .env into the shell ─────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    _env_err="$(mktemp)"
    set +e
    set -a
    source "$ENV_FILE" 2>"$_env_err"
    _env_rc=$?
    set +a
    set -e
    if [ $_env_rc -ne 0 ] || [ -s "$_env_err" ]; then
        echo "✗ failed to parse $ENV_FILE:" >&2
        sed 's/^/    /' "$_env_err" >&2
        echo "  Check for unquoted values containing spaces or shell metacharacters (&lt; &gt; &amp; \$ \`)." >&2
        rm -f "$_env_err"
        exit 1
    fi
    rm -f "$_env_err"
fi

DOMAIN="${CLOUDFLARE_DOMAIN:-}"
SUBDOMAIN="${CLOUDFLARE_SUBDOMAIN:-}"
TOKEN="${CLOUDFLARE_API_TOKEN:-}"

if [ -z "$DOMAIN" ] || [ -z "$TOKEN" ]; then
    echo "✗ CLOUDFLARE_DOMAIN and CLOUDFLARE_API_TOKEN must both be set in .env." >&2
    echo "  Re-run this script in a terminal to use the setup wizard, or edit .env." >&2
    exit 1
fi

FQDN="$DOMAIN"
[ -n "$SUBDOMAIN" ] && FQDN="$SUBDOMAIN.$DOMAIN"

# ─── dependency checks ────────────────────────────────────────────────
for bin in curl jq cloudflared openssl; do
    if ! command -v "$bin" >/dev/null; then
        echo "✗ '$bin' is required but not installed." >&2
        case "$bin" in
          jq)          echo "  Install: sudo apt install jq   (or: brew install jq)" >&2 ;;
          cloudflared) echo "  Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" >&2 ;;
        esac
        exit 1
    fi
done

# ─── Cloudflare REST helpers ──────────────────────────────────────────
CF_API="https://api.cloudflare.com/client/v4"

_cf() {
    local method="$1" path="$2" body="${3:-}"
    local args=(-sS -X "$method"
                -H "Authorization: Bearer $TOKEN"
                -H "Content-Type: application/json")
    [ -n "$body" ] && args+=(--data "$body")
    curl "${args[@]}" "$CF_API$path"
}

_cf_check() {
    local out="$1" label="$2"
    if [ "$(printf '%s' "$out" | jq -r '.success // false' 2>/dev/null)" != "true" ]; then
        echo "✗ Cloudflare API error during: $label" >&2
        printf '%s' "$out" | jq -r '.errors[]? | "    \(.code): \(.message)"' >&2 \
            || echo "    (unparseable response)" >&2
        return 1
    fi
    return 0
}

_read_state() {
    if [ -f "$1" ]; then cat "$1"; else printf ''; fi
}

# ─── resolve account_id ───────────────────────────────────────────────
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"
[ -z "$ACCOUNT_ID" ] && ACCOUNT_ID="$(_read_state "$STATE_ACCOUNT_ID")"

if [ -z "$ACCOUNT_ID" ]; then
    RES="$(_cf GET "/accounts?per_page=50")"
    if ! _cf_check "$RES" "list accounts"; then
        echo "  Account-owned (cfat_) tokens cannot enumerate accounts on their own." >&2
        echo "  Set CLOUDFLARE_ACCOUNT_ID in .env — find it in the Cloudflare" >&2
        echo "  dashboard's right sidebar (Account Home → 'Account ID')." >&2
        exit 1
    fi
    count="$(printf '%s' "$RES" | jq '.result | length')"
    if [ "$count" = "0" ]; then
        echo "✗ Token has no account access. Set CLOUDFLARE_ACCOUNT_ID in .env" >&2
        echo "  (find it in the dashboard's right sidebar), or grant the token" >&2
        echo "  'Argo Tunnel (Legacy):Edit' on the account you want to use." >&2
        exit 1
    elif [ "$count" = "1" ]; then
        ACCOUNT_ID="$(printf '%s' "$RES" | jq -r '.result[0].id')"
    else
        echo "  Token has access to $count accounts. Pick one:"
        printf '%s' "$RES" | jq -r '.result | to_entries[] | "    \(.key+1)) \(.value.name)  [\(.value.id)]"'
        printf '  Choice [1]: '
        read -r pick </dev/tty || pick=""
        pick="${pick:-1}"
        ACCOUNT_ID="$(printf '%s' "$RES" | jq -r ".result[$((pick-1))].id")"
    fi
    printf '%s' "$ACCOUNT_ID" > "$STATE_ACCOUNT_ID"
fi
echo "  account: $ACCOUNT_ID"

# ─── resolve zone_id ──────────────────────────────────────────────────
ZONE_ID="$(_read_state "$STATE_ZONE_ID")"
if [ -z "$ZONE_ID" ]; then
    RES="$(_cf GET "/zones?name=$DOMAIN")"
    _cf_check "$RES" "look up zone $DOMAIN" || exit 1
    ZONE_ID="$(printf '%s' "$RES" | jq -r '.result[0].id // empty')"
    if [ -z "$ZONE_ID" ]; then
        echo "✗ Zone '$DOMAIN' not found, or token lacks DNS:Edit on it." >&2
        exit 1
    fi
    printf '%s' "$ZONE_ID" > "$STATE_ZONE_ID"
fi
echo "  zone:    $DOMAIN ($ZONE_ID)"

# ─── find or create tunnel ────────────────────────────────────────────
echo "→ Tunnel '$TUNNEL_NAME'..."
RES="$(_cf GET "/accounts/$ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false")"
_cf_check "$RES" "list tunnels" || exit 1
TUNNEL_ID="$(printf '%s' "$RES" | jq -r '.result[0].id // empty')"

if [ -z "$TUNNEL_ID" ]; then
    echo "  creating new tunnel..."
    SECRET="$(openssl rand -base64 32 | tr -d '\n')"
    BODY="$(jq -nc --arg n "$TUNNEL_NAME" --arg s "$SECRET" \
        '{name:$n, tunnel_secret:$s, config_src:"cloudflare"}')"
    RES="$(_cf POST "/accounts/$ACCOUNT_ID/cfd_tunnel" "$BODY")"
    _cf_check "$RES" "create tunnel" || exit 1
    TUNNEL_ID="$(printf '%s' "$RES" | jq -r '.result.id')"
fi
echo "  tunnel id: $TUNNEL_ID"
printf '%s' "$TUNNEL_ID" > "$STATE_TUNNEL_ID"

# ─── fetch connector token (used by `tunnel run --token`) ────────────
RES="$(_cf GET "/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token")"
_cf_check "$RES" "fetch connector token" || exit 1
CONNECTOR_TOKEN="$(printf '%s' "$RES" | jq -r '.result')"
printf '%s' "$CONNECTOR_TOKEN" > "$STATE_TOKEN"
chmod 600 "$STATE_TOKEN"

# ─── push ingress config (remote-managed) ─────────────────────────────
echo "→ Configuring ingress: $FQDN → http://localhost:$PORT"
BODY="$(jq -nc --arg host "$FQDN" --arg svc "http://localhost:$PORT" \
    '{config:{ingress:[
        {hostname:$host, service:$svc},
        {service:"http_status:404"}
    ]}}')"
RES="$(_cf PUT "/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" "$BODY")"
_cf_check "$RES" "push ingress config" || exit 1

# ─── upsert DNS CNAME → <tunnel>.cfargotunnel.com ─────────────────────
DNS_TARGET="$TUNNEL_ID.cfargotunnel.com"
echo "→ DNS: $FQDN  CNAME  $DNS_TARGET  (proxied)"
RES="$(_cf GET "/zones/$ZONE_ID/dns_records?name=$FQDN")"
_cf_check "$RES" "list DNS records for $FQDN" || exit 1
DNS_ID="$(printf '%s' "$RES" | jq -r '.result[0].id // empty')"

BODY="$(jq -nc --arg n "$FQDN" --arg c "$DNS_TARGET" \
    '{type:"CNAME", name:$n, content:$c, proxied:true, ttl:1}')"
if [ -z "$DNS_ID" ]; then
    RES="$(_cf POST "/zones/$ZONE_ID/dns_records" "$BODY")"
else
    RES="$(_cf PUT "/zones/$ZONE_ID/dns_records/$DNS_ID" "$BODY")"
fi
_cf_check "$RES" "upsert DNS record" || exit 1

# ─── launch gunicorn in the background ────────────────────────────────
echo "→ Starting gunicorn (logs → $FLASK_LOG)"
"$PROJECT_ROOT/scripts/launch.sh" > "$FLASK_LOG" 2>&1 &
FLASK_PID=$!
echo "$FLASK_PID" > "$FLASK_PIDFILE"

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

echo
echo "  Public:  https://$FQDN"
echo "  Local:   http://localhost:$PORT"
echo "  Logs:    $FLASK_LOG"
echo "  Ctrl+C to stop."
echo

# ─── run the connector ────────────────────────────────────────────────
exec cloudflared tunnel --no-autoupdate run --token "$CONNECTOR_TOKEN"
