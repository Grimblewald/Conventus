#!/usr/bin/env bash
# Launch the site under gunicorn. Standalone — no Cloudflare in here;
# see deploy/cloudflared/ for the tunnel example.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# systemd runs with a minimal PATH — ensure uv is findable
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

WORKERS="${GUNICORN_WORKERS:-3}"
THREADS="${GUNICORN_THREADS:-32}"
PORT="${PORT:-5005}"
BIND="${BIND:-127.0.0.1:${PORT}}"

echo "→ Launching gunicorn at ${BIND} (${WORKERS} workers × ${THREADS} threads)…"

exec uv run gunicorn \
  --workers "$WORKERS" \
  --threads "$THREADS" \
  --worker-class gthread \
  --bind "$BIND" \
  --timeout 30 \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
