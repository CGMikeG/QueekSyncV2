#!/usr/bin/env bash
# QueekSync setup - one-time environment setup for Linux / WSL.
#
# Creates the project virtual environment, installs every dependency from
# requirements.txt, verifies the app can import and start, and prints next
# steps. Safe to re-run any time; dependency installation is skipped when
# requirements.txt has not changed since the last setup.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
VENV_PY="$VENV/bin/python"
STAMP=".queeksync-requirements.sha256"
LOG_DIR="$HOME/.local/share/QueekSync"

_info() { printf "[QueekSync] %s\n" "$*"; }
_die()  { printf "[QueekSync] ERROR: %s\n" "$*" >&2; exit 1; }

# ── 0 · Python ────────────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    _die "python3 not found. Install Python 3.10+ and re-run setup."
fi
PYTHON="$(command -v python3)"

# ── 1 · Virtual environment ───────────────────────────────────────────────────
if [ ! -f "$VENV_PY" ]; then
    _info "Creating virtual environment at $VENV"
    "$PYTHON" -m venv "$VENV" || _die "could not create the venv (on Debian/Ubuntu install python3-venv first)."
fi

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    _info "Installing pip into the virtual environment..."
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
"$VENV_PY" -m pip --version >/dev/null 2>&1 \
    || _die "pip is unavailable inside the venv; install the python3-venv/ensurepip package for your distro and re-run."

# ── 2 · Dependencies (stamped so re-runs are fast) ────────────────────────────
REQ_HASH="$("$PYTHON" -c "import hashlib;print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())" 2>/dev/null || true)"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null || true)" = "$REQ_HASH" ]; then
    _info "Dependencies already installed and up to date."
else
    _info "Installing dependencies from requirements.txt ..."
    if "$VENV_PY" -m pip install --upgrade -r requirements.txt; then
        printf '%s\n' "$REQ_HASH" > "$STAMP"
    else
        rm -f "$STAMP"
        _die "dependency installation failed. See the pip output above, fix it, and re-run setup."
    fi
fi

# ── 3 · Verify the app can start ──────────────────────────────────────────────
_info "Verifying the app can start..."
missing=()
for mod in PyQt6 paramiko watchdog schedule PIL darkdetect; do
    if ! "$VENV_PY" -c "import $mod" >/dev/null 2>&1; then
        missing+=("$mod")
    fi
done
if [ "${#missing[@]}" -gt 0 ]; then
    _die "missing Python packages: ${missing[*]}. Re-run setup after fixing."
fi

# Qt runtime libraries (xcb/EGL/GL) only fail when a window is created, so
# probe offscreen first.
if ! QT_QPA_PLATFORM=offscreen "$VENV_PY" - <<'PY' >/dev/null 2>&1
from PyQt6.QtWidgets import QApplication
import sys
app = QApplication(sys.argv)
PY
then
    cat >&2 <<'EOF'
[QueekSync] ERROR: PyQt6 cannot create an application (missing Qt runtime
[QueekSync] libraries). On Debian/Ubuntu install:
[QueekSync]   sudo apt-get install -y libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1
[QueekSync] On Fedora: sudo dnf install -y qt6-qtbase-gui
[QueekSync] Then re-run setup.
EOF
    exit 1
fi

# Legacy customtkinter UI needs system Tk; warn only (the Qt UI is unaffected).
if ! "$VENV_PY" -c "import tkinter" >/dev/null 2>&1; then
    printf '[QueekSync] NOTE: tkinter is missing - the legacy customtkinter UI (main.py) will not run.\n'
    printf '[QueekSync]       The PyQt6 UI (main_qt.py) is unaffected. Install python3-tk to enable the legacy UI.\n'
fi

mkdir -p "$LOG_DIR"

# ── 4 · Done ──────────────────────────────────────────────────────────────────
cat <<EOF

[QueekSync] Setup complete.
[QueekSync]   Python:        $("$VENV_PY" --version 2>&1)
[QueekSync]   Virtual env:   $VENV
[QueekSync]   Dependencies:  installed (stamp: $STAMP)
[QueekSync]
[QueekSync] Run the app with:
[QueekSync]   ./run_qt.sh        (PyQt6 UI - recommended)
[QueekSync]   ./run.sh           (legacy customtkinter UI)
[QueekSync]
[QueekSync] Windows users can double-click run.bat instead.
EOF
