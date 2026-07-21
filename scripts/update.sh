#!/usr/bin/env bash
# update.sh — atomic site update with backup & one-step revert.
#
# Usage:
#   scripts/update.sh              # backup → pull → restart
#   scripts/update.sh --revert     # restore backup → reset git → restart
#
# Backups live in var/backups/ and include app.db + a git HEAD ref.
# Only the most recent backup is kept; each run replaces it.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/var/backups"
SERVICE="cloudflared-launch.service"
PORT="${PORT:-5005}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; exit 1; }

cd "$PROJECT_ROOT"

# ── Ensure uv is on PATH (systemd runs with minimal env) ─────────────
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# ──────────────────────────────────────────────────────────────────────
# Revert  (--revert)
# ──────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--revert" ]; then
    info "Reverting to last backup…"

    if [ ! -f "$BACKUP_DIR/db.bak" ]; then
        err "No backup found at $BACKUP_DIR/db.bak — nothing to revert."
    fi

    # Restore database
    cp "$BACKUP_DIR/db.bak" "$PROJECT_ROOT/instance/app.db"
    ok "Database restored."

    # Reset git to recorded HEAD
    if [ -f "$BACKUP_DIR/git-head" ]; then
        git reset --hard "$(cat "$BACKUP_DIR/git-head")"
        ok "Git reset to $(cat "$BACKUP_DIR/git-head")."
    else
        info "No git-head recorded — skipping git reset."
    fi

    # Restart
    info "Restarting service…"
    fuser -k "$PORT/tcp" 2>/dev/null || true
    sleep 1
    systemctl --user restart "$SERVICE"
    ok "Service restarted."
    info "Revert complete."

    exit 0
fi

# ──────────────────────────────────────────────────────────────────────
# Update  (default)
# ──────────────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# 1. Record current state
info "Backing up…"
if [ -f "$PROJECT_ROOT/instance/app.db" ]; then
    cp "$PROJECT_ROOT/instance/app.db" "$BACKUP_DIR/db.bak"
    ok "Database backed up."
else
    info "No app.db found — skipping DB backup."
fi
git rev-parse HEAD > "$BACKUP_DIR/git-head"
echo "  HEAD: $(cat "$BACKUP_DIR/git-head")"

# 2. Pull
info "Pulling updates…"
if ! git pull; then
    err "git pull failed.  Your backup is at $BACKUP_DIR."
fi
ok "Pull succeeded."

# Ensure scripts are executable (git may strip the +x bit on conflict)
chmod +x scripts/*.sh 2>/dev/null || true

# 3. Migrate database
info "Applying database migrations…"
# NB: the app factory suppresses db.create_all() when booted by the `flask db`
# CLI (see app/__init__.py::_running_migration_cli), so the commands below see
# the true pre-migration schema — pending `op.create_table`s don't collide with
# tables that create_all would otherwise have pre-created from the new models.
# If the DB was restored from a raw backup, the alembic_version table
# may be missing.  Stamp the baseline so only incremental migrations
# run (avoids the initial db.create_all() creating columns that later
# migrations are already prepared to add).
if ! uv run flask db current 2>/dev/null | grep -q '^[0-9a-f]'; then
    uv run flask db stamp 4a1b2c3d4e5f
fi
uv run flask db upgrade
ok "Migrations applied."

# 4. Restart
info "Restarting service…"
fuser -k "$PORT/tcp" 2>/dev/null || true
sleep 1
systemctl --user restart "$SERVICE"
sleep 2

# 4. Verify
if systemctl --user is-active --quiet "$SERVICE"; then
    ok "Service is running."
    echo ""
    echo "  Revert this update:  scripts/update.sh --revert"
else
    err "Service failed to start.  Check: journalctl --user -u $SERVICE --no-pager -n 20"
fi
