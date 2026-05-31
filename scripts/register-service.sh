#!/usr/bin/env bash
# register-service.sh — install and start the systemd user unit for this project.
#
# Run from inside the project root (no sudo needed):
#   scripts/register-service.sh
#
# Installs a user systemd service that starts at boot (via lingering) and
# runs the Cloudflare tunnel launcher under your user account.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$PROJECT_ROOT/deploy/systemd/cloudflared-launch.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_DST="$UNIT_DIR/cloudflared-launch.service"

if [ ! -f "$UNIT_SRC" ]; then
    echo "✗ Unit template not found at $UNIT_SRC" >&2
    exit 1
fi

echo "→ Installing cloudflared-launch user service"
echo "  project: $PROJECT_ROOT"
echo "  user:    $(whoami)"

mkdir -p "$UNIT_DIR"

# The only placeholder is the project root — user services inherit the
# user's environment (PATH, home, cache dirs) naturally.
sed "s|REPLACE_WITH_PROJECT_ROOT|${PROJECT_ROOT}|g" "$UNIT_SRC" > "$UNIT_DST"

# Enable lingering so the user service starts at boot even without a login.
loginctl enable-linger

systemctl --user daemon-reload
systemctl --user enable cloudflared-launch.service

echo "→ Starting service..."
if systemctl --user start cloudflared-launch.service; then
    echo "  ✔ Service started."
    echo ""
    echo "  Check status:  systemctl --user status cloudflared-launch"
    echo "  Follow logs:   journalctl --user -xeu cloudflared-launch -f"
else
    echo "✗ Service failed to start — check logs:" >&2
    echo "  journalctl --user -xeu cloudflared-launch --no-pager -n 30" >&2
    exit 1
fi
