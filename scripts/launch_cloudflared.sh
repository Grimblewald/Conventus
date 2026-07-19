#!/usr/bin/env bash
# launch_cloudflared.sh — Conventus all-in-one launcher.
#
# First run: prompts for hostname + API token + (if account-owned
# token) Account ID + mail settings, writes them to .env.
# Subsequent runs: just goes.
#
# Auth model: scoped Cloudflare API token only.  No `cloudflared
# tunnel login`, no cert.pem, no full-account creds on the VPS.
#
# Set CF_DEBUG=1 to see every API URL the script hits.

set -euo pipefail

# ── paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
STATE_DIR="$PROJECT_ROOT/.cloudflared"
RUN_DIR="$PROJECT_ROOT/var"
FLASK_LOG="$RUN_DIR/flask.log"
FLASK_PIDFILE="$RUN_DIR/flask.pid"

mkdir -p "$STATE_DIR" "$RUN_DIR"
chmod 700 "$STATE_DIR" "$RUN_DIR" 2>/dev/null || true

PORT="${PORT:-5005}"
CF_API="https://api.cloudflare.com/client/v4"
DEBUG="${CF_DEBUG:-0}"

echo "→ Conventus launcher"
echo "  root: $PROJECT_ROOT"

# ── .env helpers ──────────────────────────────────────────────────────
_strip_quotes() {
    local v="$1"
    v="${v#\"}"; v="${v%\"}"
    v="${v#\'}"; v="${v%\'}"
    printf '%s' "$v"
}

# Wrap a value safely for `source`-ing: escape \  "  $  ` and double-quote.
_quote() {
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
    val="$(_quote "$raw")"
    if grep -qE "^#?[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null; then
        K="$key" V="$val" awk '
            BEGIN { k=ENVIRON["K"]; v=ENVIRON["V"]; done=0 }
            { if (!done && match($0, "^#?[[:space:]]*" k "=")) {
                print k "=" v; done=1
              } else { print }
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

# ── wizard ────────────────────────────────────────────────────────────
_ask() {
    # _ask VAR PROMPT [DEFAULT] [hidden]
    local var="$1" prompt="$2" default="${3:-}" hidden="${4:-}" ans
    if [ -n "$default" ]; then printf '%s [%s]: ' "$prompt" "$default"
    else                       printf '%s: ' "$prompt"
    fi
    if [ "$hidden" = "hidden" ]; then
        stty -echo 2>/dev/null || true
        read -r ans </dev/tty || ans=""
        stty echo 2>/dev/null || true
        echo ""
    else
        read -r ans </dev/tty || ans=""
    fi
    [ -z "$ans" ] && ans="$default"
    printf -v "$var" '%s' "$ans"
}

_wizard() {
    [ -t 0 ] || return 0

    if [ ! -f "$ENV_FILE" ]; then
        echo "  .env not found — copying from .env.example"
        cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    fi

    local cur_host cur_token cur_secret cur_mail
    cur_host="$(_get_env CLOUDFLARE_HOSTNAMES)"
    cur_token="$(_get_env CLOUDFLARE_API_TOKEN)"
    cur_secret="$(_get_env SECRET_KEY)"
    cur_mail="$(_get_env MAIL_BACKEND)"

    local need=0
    [ -z "$cur_host" ] && need=1
    [ -z "$cur_token" ] && need=1
    { [ -z "$cur_secret" ] || [[ "$cur_secret" == CHANGE-ME* ]]; } && need=1
    [ "$need" = 0 ] && return 0

    cat << 'INTRO'

┌──────────────────────────────────────────────────────────────────┐
│  Conventus — first-run setup                                     │
│  Press Enter to accept [defaults] in brackets.                   │
└──────────────────────────────────────────────────────────────────┘
INTRO

    # SECRET_KEY
    if [ -z "$cur_secret" ] || [[ "$cur_secret" == CHANGE-ME* ]]; then
        local s
        s="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))' 2>/dev/null \
           || openssl rand -base64 36 2>/dev/null)"
        [ -n "$s" ] && _set_env SECRET_KEY "$s" && echo "  SECRET_KEY = generated"
    fi

    # Hostname
    if [ -z "$cur_host" ]; then
        cat << 'H'

The full public hostname your site will be served at, e.g.
    zenboo.org           (apex)
    www.zenboo.org       (www subdomain)
    asmi.zenboo.org      (any subdomain)

For multiple hostnames sharing one tunnel, comma-separate them
(first one is the canonical URL).
H
        local host
        _ask host "Public hostname(s)"
        [ -n "$host" ] && _set_env CLOUDFLARE_HOSTNAMES "$host"
        cur_host="$host"
    fi

    # API token
    cur_token="$(_get_env CLOUDFLARE_API_TOKEN)"
    if [ -z "$cur_token" ]; then
        cat << 'T'

Cloudflare API token.  Recommended: account-owned (cfat_ prefix):
    Dashboard → Manage Account → Account API Tokens → Create Token
Required scopes:
    Account → Cloudflare Tunnel : Edit
    Zone    → Zone              : Read   (so we can find your zone)
    Zone    → DNS               : Edit
T
        local tok
        _ask tok "Cloudflare API token"
        [ -n "$tok" ] && { _set_env CLOUDFLARE_API_TOKEN "$tok"; cur_token="$tok"; }
    fi

    # Account ID — only required for account-owned (cfat_) tokens.
    local cur_acct
    cur_acct="$(_get_env CLOUDFLARE_ACCOUNT_ID)"
    if [ -z "$cur_acct" ] && [[ "$cur_token" == cfat_* ]]; then
        cat << 'A'

Account-owned tokens are bound to one account.  Paste your
Account ID — Cloudflare dashboard right sidebar (Account Home →
"Account ID"), 32 hex characters.
A
        local acct
        _ask acct "Cloudflare Account ID"
        [ -n "$acct" ] && _set_env CLOUDFLARE_ACCOUNT_ID "$acct"
    fi

    # Zone ID — narrow-scope tokens (DNS:Edit without Zone:Read) can't
    # enumerate zones, so accept the ID up front and skip discovery.
    local cur_zone
    cur_zone="$(_get_env CLOUDFLARE_ZONE_ID)"
    if [ -z "$cur_zone" ] && [[ "$cur_token" == cfat_* ]]; then
        cat << 'Z'

If your token has Zone:DNS:Edit but not Zone:Zone:Read (narrow
scope, recommended for blast radius), the script can't look up
your zone by name.  Paste the Zone ID to skip that step —
Dashboard → click your zone → Overview → right sidebar → API
section → "Zone ID", 32 hex chars.  Leave blank to try discovery.
Z
        local zone
        _ask zone "Cloudflare Zone ID (or blank)"
        [ -n "$zone" ] && _set_env CLOUDFLARE_ZONE_ID "$zone"
    fi

    # Mail — only ask if not already configured
    if [ -z "$cur_mail" ]; then
        echo ""
        echo "Email delivery:"
        echo "  1) smtp     — real emails (OTP, contact form, alerts)"
        echo "  2) console  — print to terminal (testing only)"
        local mc; _ask mc "Choose" "1"
        if [ "$mc" = "1" ]; then
            _set_env MAIL_BACKEND smtp
            local v
            _ask v "  SMTP host"; [ -n "$v" ] && _set_env SMTP_HOST "$v"
            _ask v "  SMTP port" "587"; _set_env SMTP_PORT "$v"
            _ask v "  SMTP username"; [ -n "$v" ] && _set_env SMTP_USER "$v"
            _ask v "  SMTP password" "" hidden; [ -n "$v" ] && _set_env SMTP_PASS "$v"
            local primary="${cur_host%%,*}"; primary="$(echo "$primary" | xargs)"
            local base="${primary#*.}"; [ -z "$base" ] && base="$primary"
            _ask v "  From address" "Conventus <noreply@${base}>"
            _set_env MAIL_FROM "$v"
        else
            _set_env MAIL_BACKEND console
        fi
    fi

    echo ""
    echo "✔  Settings saved to .env"
    echo ""
}

_wizard

# ── load .env ─────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

HOSTNAMES_RAW="${CLOUDFLARE_HOSTNAMES:-}"
TOKEN="${CLOUDFLARE_API_TOKEN:-}"
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"

if [ -z "$HOSTNAMES_RAW" ] || [ -z "$TOKEN" ]; then
    echo "✗ CLOUDFLARE_HOSTNAMES and CLOUDFLARE_API_TOKEN must be set in .env" >&2
    exit 1
fi

# Split comma-separated hostnames, trim whitespace
IFS=',' read -ra HOST_ARR <<< "$HOSTNAMES_RAW"
for i in "${!HOST_ARR[@]}"; do
    HOST_ARR[$i]="$(echo "${HOST_ARR[$i]}" | xargs)"
done
PRIMARY="${HOST_ARR[0]}"
TUNNEL_NAME="${TUNNEL_NAME:-${PRIMARY%%.*}}"

# ── deps ──────────────────────────────────────────────────────────────
for bin in curl jq cloudflared openssl; do
    command -v "$bin" >/dev/null || {
        echo "✗ '$bin' is required but not installed." >&2
        [ "$bin" = jq ]          && echo "  sudo apt install jq" >&2
        [ "$bin" = cloudflared ] && echo "  https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" >&2
        exit 1
    }
done

# ── curl wrappers — explicitly minimal, matching what works manually ──
_dbg() { [ "$DEBUG" = "1" ] && printf '    [debug] %s\n' "$*" >&2 || true; }

# GET: nothing but the Auth header. No -X, no Content-Type. Cloudflare's
# /cfd_tunnel endpoint specifically rejects Content-Type on bodyless GETs
# with a generic 10000 "Authentication error" — that's why the previous
# helper failed even though the same URL worked from a hand-typed curl.
_get() {
    _dbg "GET $1"
    curl -sS -H "Authorization: Bearer $TOKEN" "$1"
}

_post() {
    _dbg "POST $1  body=$2"
    curl -sS -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        --data "$2" "$1"
}

_put() {
    _dbg "PUT $1  body=$2"
    curl -sS -X PUT \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        --data "$2" "$1"
}

_check() {
    local resp="$1" label="$2"
    if [ "$(printf '%s' "$resp" | jq -r '.success // false' 2>/dev/null)" != "true" ]; then
        echo "✗ Cloudflare API error during: $label" >&2
        if ! printf '%s' "$resp" | jq -e '.errors' >/dev/null 2>&1; then
            echo "    raw response: $resp" >&2
        else
            printf '%s' "$resp" | jq -r '.errors[]? | "    \(.code): \(.message)"' >&2
        fi
        return 1
    fi
    return 0
}

# ── resolve account id ────────────────────────────────────────────────
if [ -z "$ACCOUNT_ID" ]; then
    RES="$(_get "$CF_API/accounts?per_page=50")"
    if ! _check "$RES" "list accounts"; then
        echo "  Account-owned (cfat_) tokens cannot enumerate accounts." >&2
        echo "  Set CLOUDFLARE_ACCOUNT_ID in .env (dashboard right sidebar)." >&2
        exit 1
    fi
    cnt="$(printf '%s' "$RES" | jq '.result | length')"
    if [ "$cnt" = 0 ]; then
        echo "✗ Token has no account access. Set CLOUDFLARE_ACCOUNT_ID in .env." >&2
        exit 1
    elif [ "$cnt" = 1 ]; then
        ACCOUNT_ID="$(printf '%s' "$RES" | jq -r '.result[0].id')"
    else
        echo "  Pick an account:"
        printf '%s' "$RES" | jq -r '.result | to_entries[] | "    \(.key+1)) \(.value.name) [\(.value.id)]"'
        printf '  Choice [1]: '
        read -r p </dev/tty || p="1"; p="${p:-1}"
        ACCOUNT_ID="$(printf '%s' "$RES" | jq -r ".result[$((p-1))].id")"
    fi
fi
echo "  account: $ACCOUNT_ID"

# ── derive zone for each hostname ─────────────────────────────────────
# Priority for each hostname:
#   1. cached state file (.cloudflared/zone-<hostname>)
#   2. CLOUDFLARE_ZONE_ID from .env (only valid if all hostnames share one zone)
#   3. API discovery: walk up labels asking /zones?name=...
#   4. interactive prompt — last resort, saves to .env
declare -A HOST_ZONE

_save_zone_cache() {
    local hostname="$1" zid="$2"
    printf '%s' "$zid" > "$STATE_DIR/zone-${hostname}"
    chmod 600 "$STATE_DIR/zone-${hostname}" 2>/dev/null || true
}

_zone_from_state() {
    local f="$STATE_DIR/zone-$1"
    [ -f "$f" ] && cat "$f" || printf ''
}

# Walks labels.  Echoes "zone_id" on success, "" on not-found,
# and writes any Cloudflare error to FD 3 (so caller can surface it).
_discover_zone() {
    local name="$1" resp zid last_err=""
    while :; do
        resp="$(_get "$CF_API/zones?name=$name")"
        if [ "$(printf '%s' "$resp" | jq -r '.success // false' 2>/dev/null)" = "true" ]; then
            zid="$(printf '%s' "$resp" | jq -r '.result[0].id // empty' 2>/dev/null)"
            if [ -n "$zid" ]; then
                printf '%s' "$zid"
                return 0
            fi
        else
            last_err="$(printf '%s' "$resp" | jq -r '.errors[]? | "\(.code): \(.message)"' 2>/dev/null | head -1)"
        fi
        local rest="${name#*.}"
        [ "$rest" = "$name" ] && break
        name="$rest"
    done
    [ -n "$last_err" ] && printf '%s' "$last_err" >&3
    return 1
}

_prompt_zone_id() {
    local hostname="$1" zid
    [ -t 0 ] || return 1
    cat >&2 << EOF

  Cloudflare couldn't tell me the zone for '$hostname' with this token.
  This usually means the token lacks Zone:Zone:Read, OR the zone scope
  on the token doesn't cover this hostname.

  Workaround: paste the Zone ID directly (Dashboard → click your zone
  '$hostname' → Overview → right sidebar → API → "Zone ID"), 32 hex chars.
  Leave blank to abort.
EOF
    printf '  Zone ID for %s: ' "$hostname" >&2
    read -r zid </dev/tty || zid=""
    [ -z "$zid" ] && return 1
    printf '%s' "$zid"
}

for h in "${HOST_ARR[@]}"; do
    zid="$(_zone_from_state "$h")"
    if [ -z "$zid" ] && [ -n "${CLOUDFLARE_ZONE_ID:-}" ]; then
        zid="$CLOUDFLARE_ZONE_ID"
    fi
    if [ -z "$zid" ]; then
        echo "→ Looking up zone for $h..."
        exec 3>/tmp/conventus-zonerr.$$
        zid="$(_discover_zone "$h")" || true
        exec 3>&-
        if [ -z "$zid" ] && [ -s /tmp/conventus-zonerr.$$ ]; then
            echo "    Cloudflare said: $(cat /tmp/conventus-zonerr.$$)" >&2
        fi
        rm -f /tmp/conventus-zonerr.$$
    fi
    if [ -z "$zid" ]; then
        zid="$(_prompt_zone_id "$h")" || {
            echo "✗ No zone resolved for '$h'.  Aborting." >&2
            exit 1
        }
        # Persist so we don't ask again
        if [ "${#HOST_ARR[@]}" = 1 ]; then
            _set_env CLOUDFLARE_ZONE_ID "$zid"
        fi
    fi
    HOST_ZONE[$h]="$zid"
    _save_zone_cache "$h" "$zid"
    echo "  $h  →  zone $zid"
done

# ── find or create tunnel ─────────────────────────────────────────────
echo "→ Tunnel '$TUNNEL_NAME'"
RES="$(_get "$CF_API/accounts/$ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false")"
_check "$RES" "list tunnels" || exit 1
TUNNEL_ID="$(printf '%s' "$RES" | jq -r '.result[0].id // empty')"

if [ -z "$TUNNEL_ID" ]; then
    echo "  creating..."
    SECRET="$(openssl rand -base64 32 | tr -d '\n')"
    BODY="$(jq -nc --arg n "$TUNNEL_NAME" --arg s "$SECRET" \
        '{name:$n, tunnel_secret:$s, config_src:"cloudflare"}')"
    RES="$(_post "$CF_API/accounts/$ACCOUNT_ID/cfd_tunnel" "$BODY")"
    _check "$RES" "create tunnel" || exit 1
    TUNNEL_ID="$(printf '%s' "$RES" | jq -r '.result.id')"
fi
echo "  id: $TUNNEL_ID"

# ── connector token (used by `tunnel run --token`) ────────────────────
RES="$(_get "$CF_API/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token")"
_check "$RES" "fetch connector token" || exit 1
CONNECTOR_TOKEN="$(printf '%s' "$RES" | jq -r '.result')"
printf '%s' "$CONNECTOR_TOKEN" > "$STATE_DIR/connector-token"
chmod 600 "$STATE_DIR/connector-token"

# ── push ingress config (remote-managed) ──────────────────────────────
HOSTS_JSON="$(printf '%s\n' "${HOST_ARR[@]}" | jq -R . | jq -sc .)"
INGRESS="$(jq -nc --argjson hosts "$HOSTS_JSON" --arg svc "http://localhost:$PORT" \
    '{config:{ingress: ($hosts | map({hostname:., service:$svc}) + [{service:"http_status:404"}])}}')"
echo "→ Ingress: ${HOST_ARR[*]} → http://localhost:$PORT"
RES="$(_put "$CF_API/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" "$INGRESS")"
_check "$RES" "push ingress config" || exit 1

# ── upsert DNS CNAME for each hostname ────────────────────────────────
DNS_TARGET="$TUNNEL_ID.cfargotunnel.com"
for h in "${HOST_ARR[@]}"; do
    zid="${HOST_ZONE[$h]}"
    echo "→ DNS: $h → $DNS_TARGET (proxied)"
    RES="$(_get "$CF_API/zones/$zid/dns_records?name=$h")"
    _check "$RES" "list DNS records for $h" || exit 1
    DNS_ID="$(printf '%s' "$RES" | jq -r '.result[0].id // empty')"
    BODY="$(jq -nc --arg n "$h" --arg c "$DNS_TARGET" \
        '{type:"CNAME", name:$n, content:$c, proxied:true, ttl:1}')"
    if [ -z "$DNS_ID" ]; then
        RES="$(_post "$CF_API/zones/$zid/dns_records" "$BODY")"
    else
        RES="$(_put "$CF_API/zones/$zid/dns_records/$DNS_ID" "$BODY")"
    fi
    _check "$RES" "upsert DNS for $h" || exit 1
done

# ── launch gunicorn in background ─────────────────────────────────────
echo "→ Starting gunicorn (logs: $FLASK_LOG)"
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
echo "  Public:  https://$PRIMARY"
for ((i=1; i<${#HOST_ARR[@]}; i++)); do
    echo "           https://${HOST_ARR[$i]}"
done
echo "  Local:   http://localhost:$PORT"
echo "  Logs:    $FLASK_LOG"
echo "  Ctrl+C to stop."
echo

exec cloudflared tunnel --no-autoupdate run --token "$CONNECTOR_TOKEN"
