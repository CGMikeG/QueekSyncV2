#!/usr/bin/env python3
"""
main_compact_qt.py — compact QueekSync launcher (Peer Sync + Settings).

Same application object as main_qt.py (profile manager, scheduler, watcher and
the sync engines are all wired up), just a smaller fixed-dark window. The
sidebar itself is trimmed in src/ui_qt/sidebar.py, and the Peer Sync sections
are collapsible, so nothing here duplicates the app's wiring.

Never build PeerSyncPanel by hand: it needs the QueekSyncApp window for
`_app.profile_mgr` / `_app.save_profile` / `_app.start_sync`.
"""

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))


def main() -> None:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.app import QueekSyncApp
    from ui_qt.theme import build_qss

    app = QApplication(sys.argv)
    app.setApplicationName("QueekSync")
    app.setOrganizationName("QueekSync")

    window = QueekSyncApp()
    window.resize(1060, 780)                        # compact: fits 1080p, no scrolling
    window.setMinimumSize(900, 620)
    window.setStyleSheet(build_qss(light=False))    # compact launcher is dark-only
    window.navigate("peer")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
