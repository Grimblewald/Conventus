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

# Static-build fallback version. The official install script's gnu build is
# dynamically linked (notably against libssl 1.1, which Ubuntu 22.04+ no
# longer ships), so when it won't run we fall back to the fully static musl
# release, which has no shared-library dependencies at all.
MUSL_VERSION="0.16.9"

TMP_DIR="$(mktemp -d)"
WARM_DIR=""
trap 'rm -rf "$TMP_DIR" "$WARM_DIR"' EXIT

install_official() {
    info "Installing tectonic to $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR"
    if ! (cd "$TMP_DIR" && curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh); then
        die "tectonic install script failed. Package-manager alternative: 'apt install tectonic' (Ubuntu 22.04+), 'brew install tectonic', or 'cargo install tectonic'."
    fi
    [ -f "$TMP_DIR/tectonic" ] || die "Install script ran but no tectonic binary appeared in $TMP_DIR."
    mv "$TMP_DIR/tectonic" "$BIN"
    chmod 755 "$BIN"
    ok "tectonic installed at $BIN."
}

install_musl_static() {
    local url="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${MUSL_VERSION}/tectonic-${MUSL_VERSION}-x86_64-unknown-linux-musl.tar.gz"
    info "Fetching the static musl build ${MUSL_VERSION} ..."
    if ! curl --proto '=https' --tlsv1.2 -fsSL -o "$TMP_DIR/tectonic-musl.tar.gz" "$url"; then
        die "Download of the static musl build failed ($url)."
    fi
    tar -xzf "$TMP_DIR/tectonic-musl.tar.gz" -C "$TMP_DIR" tectonic
    mkdir -p "$INSTALL_DIR"
    mv "$TMP_DIR/tectonic" "$BIN"
    chmod 755 "$BIN"
    ok "Static musl tectonic installed at $BIN."
}

# `--version` succeeding is the health gate; its stderr is preserved so a
# loader failure (missing shared library) is shown, not swallowed.
verify_bin() {
    VERSION="$("$BIN" --version 2>"$TMP_DIR/verify.err" | head -n1 || true)"
    [ -n "$VERSION" ]
}

# ── 1. Install (idempotent — an existing binary is verified, not trusted) ──
if [ -x "$BIN" ] && [ "$FORCE" != "--force" ]; then
    info "tectonic already installed at $BIN — skipping download (pass --force to reinstall)."
else
    install_official
fi

# ── 2. Verify it runs; fall back to the static musl build if not ─────────
if ! verify_bin; then
    err "'$BIN --version' failed:"
    sed 's/^/    /' "$TMP_DIR/verify.err" >&2 || true
    if [ "$(uname -m)" = "x86_64" ]; then
        info "The gnu build likely needs shared libraries this host lacks (e.g."
        info "libssl 1.1 on Ubuntu 22.04+). Retrying with the static musl build."
        install_musl_static
        if ! verify_bin; then
            err "'$BIN --version' still failing:"
            sed 's/^/    /' "$TMP_DIR/verify.err" >&2 || true
            die "Could not obtain a working tectonic. Alternatives: 'apt install tectonic', 'brew install tectonic', or 'cargo install tectonic'."
        fi
    else
        die "No static fallback for $(uname -m). Alternatives: 'apt install tectonic', 'brew install tectonic', or 'cargo install tectonic'."
    fi
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
