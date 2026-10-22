#!/usr/bin/env bash
# Update the site, or put the previous version back.
#
#   scripts/update.sh                  restore point → pull → dependencies → migrate → restart
#   scripts/update.sh --revert         put back the newest restore point (asks first)
#   scripts/update.sh --revert --yes   ... without asking
#   scripts/update.sh list             list restore points
#
# The work is done by scripts/site_update.py — the same code Admin → Update
# runs — which uses only Python's standard library and never imports the app,
# so --revert works even when the new version will not start.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# systemd runs with a minimal environment — make uv findable.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if command -v python3 >/dev/null 2>&1; then
    PY=(python3)
else
    PY=(uv run --no-sync python)
fi

case "${1:-}" in
    "")       exec "${PY[@]}" scripts/site_update.py update ;;
    --revert) shift; exec "${PY[@]}" scripts/site_update.py revert "$@" ;;
    *)        exec "${PY[@]}" scripts/site_update.py "$@" ;;
esac
