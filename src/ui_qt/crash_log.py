"""
Crash evidence for the PyQt6 app.

A raised exception inside a Qt slot does not print-and-continue: PyQt calls
qFatal, so the process dies with SIGABRT and the user just sees the window
vanish — with nothing in the journal when the app was started from a .desktop
entry. This installs two nets before the window is built:

- ``sys.excepthook`` appends the Python traceback (this fires for slot errors
  before PyQt aborts).
- ``faulthandler`` appends the C-level stack on SIGABRT/SIGSEGV (covers crashes
  that never reach Python).

Both write to ``~/.local/share/QueekSync/crash.log`` (or ``$XDG_DATA_HOME``),
so the next "the app crashed" report has something to read.

Usage, as the first thing in a launcher::

    from ui_qt.crash_log import install_crash_log
    install_crash_log()
"""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from datetime import datetime


def crash_log_path() -> str:
    """Where crashes are appended (also handy for a 'Show crash log' button)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    folder = os.path.join(base, "QueekSync")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.expanduser("~")
    return os.path.join(folder, "crash.log")


def install_crash_log() -> str:
    """Start recording tracebacks and hard crashes. Returns the log path."""
    path = crash_log_path()
    try:
        handle = open(path, "a", buffering=1, encoding="utf-8")
    except OSError:
        return path

    handle.write(f"\n=== QueekSync start {datetime.now():%Y-%m-%d %H:%M:%S} "
                 f"(pid {os.getpid()}) ===\n")
    handle.flush()

    # hard crashes (SIGABRT from qFatal, SIGSEGV, ...) dump every thread's stack
    faulthandler.enable(file=handle, all_threads=True)

    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            handle.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} unhandled exception ---\n")
            handle.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
            handle.flush()
        finally:
            previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    return path
