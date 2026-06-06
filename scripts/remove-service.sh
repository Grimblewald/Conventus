#!/usr/bin/env bash
# remove-service.sh — stop and uninstall the systemd user units.
set -euo pipefail

SERVICES=("cloudflared-launch" "society-site-backup" "society-site-healthcheck")

for svc in "${SERVICES[@]}"; do
    if systemctl --user is-enabled --quiet "${svc}.service" 2>/dev/null; then
        echo "→ Removing ${svc}…"
        systemctl --user stop "${svc}.service" 2>/dev/null || true
        systemctl --user stop "${svc}.timer" 2>/dev/null || true
        systemctl --user disable "${svc}.service" 2>/dev/null || true
        systemctl --user disable "${svc}.timer" 2>/dev/null || true
        rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${svc}.service" \
              "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${svc}.timer"
        echo "  ✓ removed"
    else
        echo "  ${svc} — not installed, skipping"
    fi
done

systemctl --user daemon-reload
echo "Done."
