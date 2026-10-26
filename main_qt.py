#!/usr/bin/env python3
"""
QueekSync - Professional File Synchronization Tool (PyQt6 UI)
Cross-platform file sync application with a modern glass UI.

This is the PyQt6-native launcher. The old customtkinter UI remains
available via main.py.
"""

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))


def main() -> None:
    from PyQt6.QtWidgets import QApplication

    from ui_qt.app import QueekSyncApp
    from ui_qt.crash_log import install_crash_log

    crash_log = install_crash_log()   # next crash leaves a traceback, not just a vanished window
    print(f"QueekSync: crash log -> {crash_log}", flush=True)

    app = QApplication(sys.argv)
    app.setApplicationName("QueekSync")
    app.setOrganizationName("QueekSync")

    window = QueekSyncApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
