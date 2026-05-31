#!/usr/bin/env bash
# register-service.sh — install and start the systemd unit for this project.
#
# Run from inside the project root:
#   sudo scripts/register-service.sh
#
# The script auto-detects the project directory and the invoking user,
# substitutes them into the unit template, and enables + starts the service.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$PROJECT_ROOT/deploy/systemd/cloudflared-launch.service"
UNIT_DST="/etc/systemd/system/cloudflared-launch.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "✗ Must run as root (sudo)." >&2
    exit 1
fi

if [ ! -f "$UNIT_SRC" ]; then
    echo "✗ Unit template not found at $UNIT_SRC" >&2
    exit 1
fi

# Resolve the user who invoked sudo.  SUDO_USER is set by sudo; if not
# present (e.g. logged in as root directly), default to the owner of the
# project directory.
USER="${SUDO_USER:-$(stat -c '%U' "$PROJECT_ROOT" 2>/dev/null || echo root)}"
HOME_DIR="$(eval echo "~$USER")"

echo "→ Installing cloudflared-launch.service"
echo "  project: $PROJECT_ROOT"
echo "  user:    $USER"
echo "  home:    $HOME_DIR"

# Substitute placeholders.  We use '|' as the sed delimiter because project
# paths contain '/' characters.
sed \
    -e "s|REPLACE_WITH_YOUR_USER|${USER}|g" \
    -e "s|REPLACE_WITH_PROJECT_ROOT|${PROJECT_ROOT}|g" \
    -e "s|REPLACE_WITH_USER_HOME|${HOME_DIR}|g" \
    "$UNIT_SRC" > "$UNIT_DST"

chmod 644 "$UNIT_DST"

systemctl daemon-reload
systemctl enable cloudflared-launch.service

echo "→ Starting service..."
if systemctl start cloudflared-launch.service; then
    echo "  ✔ Service started."
    echo ""
    echo "  Check status:  systemctl status cloudflared-launch"
    echo "  Follow logs:   journalctl -xeu cloudflared-launch -f"
else
    echo "✗ Service failed to start — check logs:" >&2
    echo "  journalctl -xeu cloudflared-launch --no-pager -n 30" >&2
    exit 1
fi
