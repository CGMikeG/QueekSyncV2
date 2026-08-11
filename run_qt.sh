#!/usr/bin/env bash
# QueekSync launcher for Linux / WSL (PyQt6 UI).
#
# Bootstraps the app's own virtual environment on first run: creates .venv
# (if missing), installs every dependency from requirements.txt (skipped when
# nothing changed since the last run), then launches the app.
set -e

LOG="$HOME/.local/share/QueekSync/queeksync.log"
mkdir -p "$(dirname "$LOG")"

_die() {
    echo ""
    echo "══════════════════════════════════════════"
    echo "  QueekSync failed to start. Error log:"
    echo "  $LOG"
    echo "══════════════════════════════════════════"
    echo ""
    tail -30 "$LOG" 2>/dev/null || true
    if [ -t 1 ]; then
        read -rp "Press Enter to close..." _
    fi
    exit 1
}
trap _die ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── WSLg display setup ───────────────────────────────────────────────────────
if grep -qi microsoft /proc/version 2>/dev/null; then
    export DISPLAY="${DISPLAY:-:0}"
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/mnt/wslg/runtime-dir}"
fi

# ── Own virtual environment ──────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo "[QueekSync] ERROR: python3 not found. Install Python 3.10+ and re-run." >&2
    exit 1
fi

VENV="$SCRIPT_DIR/.venv"
VENV_PY="$VENV/bin/python"
STAMP=".queeksync-requirements.sha256"

if [ ! -f "$VENV_PY" ]; then
    echo "[QueekSync] Creating virtual environment..."
    python3 -m venv "$VENV"
fi

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo "[QueekSync] Installing pip..."
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

# Install all dependencies (requirements.txt) when needed; the stamp keeps
# repeat launches fast. This is what guarantees paramiko/PyQt6/... exist.
REQ_HASH="$(python3 -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" 2>/dev/null || true)"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$REQ_HASH" ]; then
    :
else
    echo "[QueekSync] Installing dependencies into the app virtual environment..."
    "$VENV_PY" -m pip install --quiet --upgrade -r requirements.txt
    printf '%s\n' "$REQ_HASH" > "$STAMP"
fi

# ── Launch ───────────────────────────────────────────────────────────────────
echo "[QueekSync] Starting (PyQt6 UI)... (log: $LOG)"
"$VENV_PY" main_qt.py "$@" 2>&1 | tee "$LOG"
