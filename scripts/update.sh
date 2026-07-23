#!/usr/bin/env bash
# update.sh — site update with timestamped, non-destructive backups.
#
# Usage:
#   scripts/update.sh              # snapshot → pull → migrate → restart
#   scripts/update.sh --revert     # roll back code + DB to the last snapshot
#   scripts/update.sh --revert --yes   # ... without the confirmation prompt
#
# Every run writes a NEW timestamped snapshot under var/backups/<UTC>/
# (app.db + the git HEAD it went with); snapshots are never overwritten, and
# the newest ten are kept. --revert restores the most recent snapshot, but
# ALWAYS copies the current database aside first (a *-pre-revert snapshot), so
# a revert can itself be undone and no state is ever destroyed irrecoverably.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/var/backups"
DB="$PROJECT_ROOT/instance/app.db"
SERVICE="cloudflared-launch.service"
PORT="${PORT:-5005}"
KEEP="${UPDATE_KEEP_SNAPSHOTS:-10}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'; NC='\033[0m'
info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*"; exit 1; }

cd "$PROJECT_ROOT"

# systemd runs with a minimal env — make uv findable.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Write a snapshot of the current DB + git HEAD into a fresh timestamped dir.
# Never clobbers: the directory name carries a UTC timestamp (plus an optional
# label). Echoes the snapshot path.
take_snapshot() {
    local label="${1:-}"
    local stamp; stamp="$(date -u +%Y%m%d-%H%M%S)"
    local dir="$BACKUP_DIR/${stamp}${label:+-$label}"
    # A same-second second snapshot must not merge into the first.
    local n=1
    while [ -e "$dir" ]; do dir="$BACKUP_DIR/${stamp}${label:+-$label}-$n"; n=$((n+1)); done
    mkdir -p "$dir"
    if [ -f "$DB" ]; then
        # Use sqlite's own backup when available so an in-flight write can't
        # produce a torn copy; fall back to cp otherwise.
        if command -v sqlite3 >/dev/null 2>&1; then
            sqlite3 "$DB" ".backup '$dir/app.db'" 2>/dev/null || cp "$DB" "$dir/app.db"
        else
            cp "$DB" "$dir/app.db"
        fi
    fi
    git rev-parse HEAD > "$dir/git-head" 2>/dev/null || true
    echo "$dir"
}

# Newest snapshot dir that actually holds a database, excluding the
# pre-revert safety copies (we roll back TO an update snapshot, not to the
# copy a previous revert set aside). Empty if none.
latest_snapshot() {
    local d
    for d in $(ls -1dr "$BACKUP_DIR"/*/ 2>/dev/null); do
        # Skip every pre-revert copy, including the same-second collision form
        # <stamp>-pre-revert-N/ — a plain *-pre-revert/ glob would miss those.
        case "$d" in *-pre-revert/|*-pre-revert-[0-9]*/) continue;; esac
        [ -f "$d/app.db" ] && { echo "${d%/}"; return; }
    done
}

prune_snapshots() {
    # Keep the newest $KEEP update snapshots; pre-revert copies are kept
    # separately (they are the only record of a rolled-back live state).
    local d keep_list
    keep_list=$(ls -1dr "$BACKUP_DIR"/*/ 2>/dev/null \
        | grep -Ev -- '-pre-revert(-[0-9]+)?/$' || true)
    echo "$keep_list" | tail -n +"$((KEEP+1))" | while read -r d; do
        [ -n "$d" ] && rm -rf "$d"
    done
}

# ──────────────────────────────────────────────────────────────────────
# Revert  (--revert [--yes])
# ──────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--revert" ]; then
    ASSUME_YES=0
    [ "${2:-}" = "--yes" ] && ASSUME_YES=1

    SNAP="$(latest_snapshot)"
    # Legacy single-slot layout (pre-2026-07 update.sh) — restore from it if
    # no timestamped snapshot exists yet.
    if [ -z "$SNAP" ] && [ -f "$BACKUP_DIR/db.bak" ]; then
        SNAP="__legacy__"
    fi
    [ -z "$SNAP" ] && err "No snapshot with a database found under $BACKUP_DIR — nothing to revert to."

    if [ "$SNAP" = "__legacy__" ]; then
        SNAP_DB="$BACKUP_DIR/db.bak"
        SNAP_HEAD_FILE="$BACKUP_DIR/git-head"
        SNAP_DESC="legacy backup ($(date -u -r "$SNAP_DB" +%Y-%m-%d\ %H:%M:%S 2>/dev/null || echo unknown\ time) UTC)"
    else
        SNAP_DB="$SNAP/app.db"
        SNAP_HEAD_FILE="$SNAP/git-head"
        SNAP_DESC="$(basename "$SNAP") ($(date -u -r "$SNAP_DB" +%Y-%m-%d\ %H:%M:%S 2>/dev/null || echo unknown\ time) UTC)"
    fi

    warn "Reverting will replace the current database and code with: $SNAP_DESC"
    warn "Any data written since that snapshot will be rolled back."
    if [ "$ASSUME_YES" -ne 1 ]; then
        printf "Continue? [y/N] "
        read -r reply
        [ "$reply" = "y" ] || [ "$reply" = "Y" ] || err "Revert cancelled — nothing changed."
    fi

    # Safety net: preserve the CURRENT live DB before overwriting it, so this
    # revert is itself reversible. This is the guarantee the old script lacked.
    if [ -f "$DB" ]; then
        SAFE="$(take_snapshot pre-revert)"
        ok "Current database preserved at ${SAFE#$PROJECT_ROOT/} before rollback."
    fi

    cp "$SNAP_DB" "$DB"
    ok "Database restored from $SNAP_DESC."

    if [ -f "$SNAP_HEAD_FILE" ] && [ -s "$SNAP_HEAD_FILE" ]; then
        git reset --hard "$(cat "$SNAP_HEAD_FILE")"
        ok "Code reset to $(cat "$SNAP_HEAD_FILE")."
    else
        info "No git HEAD recorded in the snapshot — leaving code as-is."
    fi

    info "Restarting service…"
    fuser -k "$PORT/tcp" 2>/dev/null || true
    sleep 1
    systemctl --user restart "$SERVICE"
    ok "Revert complete."
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────
# Update  (default)
# ──────────────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# 1. Snapshot current state (timestamped — never overwrites a prior snapshot).
info "Snapshotting current state…"
SNAP="$(take_snapshot)"
ok "Snapshot saved to ${SNAP#$PROJECT_ROOT/}"
[ -f "$SNAP/app.db" ] || info "No app.db found — snapshot records code only."
prune_snapshots

# 2. Pull
info "Pulling updates…"
if ! git pull; then
    err "git pull failed. Your snapshot is at ${SNAP#$PROJECT_ROOT/}."
fi
ok "Pull succeeded."

# git may strip the +x bit on conflict.
chmod +x scripts/*.sh 2>/dev/null || true

# 3. Migrate database
info "Applying database migrations…"
# The app factory suppresses db.create_all() when booted by the `flask db`
# CLI (see app/__init__.py::_running_migration_cli), so the commands below see
# the true pre-migration schema — pending create_table's don't collide with
# tables create_all would otherwise pre-create from the new models.
# If the DB was restored from a raw backup the alembic_version table may be
# missing; stamp the baseline so only incremental migrations run.
if ! uv run flask db current 2>/dev/null | grep -q '^[0-9a-f]'; then
    uv run flask db stamp 4a1b2c3d4e5f
fi
uv run flask db upgrade
ok "Migrations applied."

# 3b. tectonic presence check — the PDF document system (invoices, receipts,
# adjustment notes) has no plain-format fallback, so a missing tectonic must be
# loud. Never auto-installs, never fails the update.
if ! command -v tectonic >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/tectonic" ]; then
    echo ""
    warn "tectonic not found. PDF documents (invoices/receipts/adjustment"
    echo "  notes) cannot be generated until it's installed. Run:"
    echo "    scripts/install-tectonic.sh"
    echo ""
fi

# 4. Restart
info "Restarting service…"
fuser -k "$PORT/tcp" 2>/dev/null || true
sleep 1
systemctl --user restart "$SERVICE"
sleep 2

# 5. Verify
if systemctl --user is-active --quiet "$SERVICE"; then
    ok "Service is running."
    echo ""
    echo "  Roll back this update:  scripts/update.sh --revert"
else
    err "Service failed to start. Check: journalctl --user -u $SERVICE --no-pager -n 20"
fi
