#!/bin/bash
# QueekSync compact launcher — Peer Sync + Settings only, collapsible sections.
# Clickable: double-click or right-click → Run as a Program.
cd "$(dirname "$(realpath "$0")")" || exit 1
export DISPLAY=:0
export QT_QPA_PLATFORM=xcb
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python main_compact_qt.py
