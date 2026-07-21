#!/usr/bin/env bash
# install-tectonic.sh — install (or verify) the tectonic binary and pre-warm
# its package cache so the first live PDF render isn't a slow/fragile cold
# fetch.
#
# tectonic is a HARD deploy dependency for the PDF document system (invoice /
# receipt / adjustment note) — there is deliberately no plain-format fallback
# (see INVOICE_PDF_PLAN.md §7/§11), so a missing or broken tectonic must be
# loud, not silently degrade a document send.
#
# Usage:
#   scripts/install-tectonic.sh              # install if absent, then warm-check
#   scripts/install-tectonic.sh --force       # reinstall even if already present
#
# Installs to ~/.local/bin/tectonic via the official install script
# (https://drop-sh.fullyjustified.net, see
# https://tectonic-typesetting.github.io/en-US/install.html). That script
# always fetches the latest release and drops the binary in the current
# directory — it has no version-pin flag — so this wrapper cd's into a temp
# dir first and moves the result into place. Idempotent: with no binary
# present it installs; with one already present it skips the download
# (report-only) unless --force is given, so re-running this script (e.g. from
# scripts/update.sh, or by hand) never disturbs a working install.
#
# Alternative: most distros package tectonic too (e.g. `apt install
# tectonic` on Ubuntu 22.04+, `brew install tectonic`, or `cargo install
# tectonic`) if you'd rather manage it via a system package manager — any of
# those satisfy the app equally well as long as `tectonic` ends up on PATH or
# at ~/.local/bin/tectonic (see TECTONIC_BIN in app/services/documents.py).
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*" >&2; }
die()   { err "$*"; exit 1; }

INSTALL_DIR="$HOME/.local/bin"
BIN="$INSTALL_DIR/tectonic"
FORCE="${1:-}"

# Declared upfront (possibly unused) so the single EXIT trap below never
# references an unset variable under `set -u`.
TMP_DIR=""
WARM_DIR=""
trap 'rm -rf "$TMP_DIR" "$WARM_DIR"' EXIT

# ── 1. Install (idempotent) ─────────────────────────────────────────────
if [ -x "$BIN" ] && [ "$FORCE" != "--force" ]; then
    info "tectonic already installed at $BIN — skipping download (pass --force to reinstall)."
else
    info "Installing tectonic to $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR"

    TMP_DIR="$(mktemp -d)"

    if ! (cd "$TMP_DIR" && curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh); then
        die "tectonic install script failed. Package-manager alternative: 'apt install tectonic' (Ubuntu 22.04+), 'brew install tectonic', or 'cargo install tectonic'."
    fi

    if [ ! -f "$TMP_DIR/tectonic" ]; then
        die "Install script ran but no tectonic binary appeared in $TMP_DIR."
    fi

    mv "$TMP_DIR/tectonic" "$BIN"
    chmod 755 "$BIN"
    ok "tectonic installed at $BIN."
fi

# ── 2. Echo the version actually in place ───────────────────────────────
VERSION="$("$BIN" --version 2>/dev/null | head -n1 || true)"
if [ -z "$VERSION" ]; then
    die "tectonic is at $BIN but '$BIN --version' failed — binary looks broken."
fi
info "Version: $VERSION"

# ── 3. Pre-warm the package cache ───────────────────────────────────────
# Compiles a tiny document exercising the same package set as the real
# document skeleton (app/latex/document.tex: fontenc, geometry, helvet,
# graphicx, array, tabularx, xcolor) so the packages/fonts land in tectonic's
# bundle cache now, not on the first real invoice/receipt render.
info "Pre-warming the tectonic package cache ..."
WARM_DIR="$(mktemp -d)"

cat > "$WARM_DIR/warmup.tex" <<'EOF'
\documentclass[11pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[margin=22mm]{geometry}
\usepackage{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage{graphicx}
\usepackage{array}
\usepackage{tabularx}
\usepackage{xcolor}
\begin{document}
\textcolor{blue}{Tectonic package cache warm-up.}
\begin{tabularx}{\linewidth}{Xl}
  Warm & OK \\
\end{tabularx}
\end{document}
EOF

if "$BIN" --outdir "$WARM_DIR" "$WARM_DIR/warmup.tex" > "$WARM_DIR/tectonic.log" 2>&1 \
        && [ -f "$WARM_DIR/warmup.pdf" ]; then
    ok "Package cache warm — compiled a test document successfully."
else
    err "Pre-warm compile FAILED. tectonic is installed but cannot compile the"
    err "document system's package set — invoices/receipts will fail to render."
    err "tectonic output:"
    cat "$WARM_DIR/tectonic.log" >&2 2>/dev/null || true
    die "Fix network/proxy access to the tectonic bundle host, or re-run with --force."
fi

echo ""
ok "tectonic ready ($VERSION, $BIN)."
if ! command -v tectonic >/dev/null 2>&1; then
    info "Note: $INSTALL_DIR is not on PATH for this shell — the app finds it via"
    info "the ~/.local/bin fallback (see _resolve_tectonic in app/services/documents.py),"
    info "but you may want '$INSTALL_DIR' on PATH for interactive use too."
fi
