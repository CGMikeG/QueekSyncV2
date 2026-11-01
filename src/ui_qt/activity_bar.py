"""
Docked activity bar — the compact-mode replacement for the floating window.

Same public API as the floating StatusPopup (set_status / begin_job / finish /
on_sync_event / toggle / toggle_log), so QueekSyncApp reports activity the same
way in both builds; this one sits pinned at the bottom of the window as a single
line and expands its log upwards, so it never covers the tables.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui_qt import theme as T
from ui_qt.widgets import (GhostButton, IDLE_HIDE_ERROR_MS, IDLE_HIDE_MS, LogViewer,
                           ProgressBar, StatusBadge, attach_tooltip, completion_footer,
                           completion_headline)

_KIND_COLORS: Dict[str, str] = {
    "info":    T.TEXT,
    "compare": T.ACCENT,
    "copy":    T.INFO,
    "delete":  T.WARNING,
    "skip":    T.TEXT_MUTED,
    "success": T.SUCCESS,
    "error":   T.ERROR,
    "warning": T.WARNING,
}

# After a job finishes the bar folds itself away, so an idle window is not left
# with a strip of stale "done" text. Delays live in ui_qt.widgets.
_IDLE_HIDE_MS = IDLE_HIDE_MS
_IDLE_HIDE_ERROR_MS = IDLE_HIDE_ERROR_MS

_COUNTED = ("compare", "copy", "delete", "skip", "error")


class ActivityBar(QFrame):
    """One pinned line: state badge, what it is doing, progress, event log."""

    LOG_H = 108

    def __init__(self, app: QWidget) -> None:
        super().__init__(app)
        self._app = app
        self.setObjectName("ActivityBar")
        self.setStyleSheet(
            f"QFrame#ActivityBar {{ background-color: {T.BG_PANEL};"
            f" border-top: 1px solid {T.BORDER}; }}"
        )
        self._expanded = False
        self._counts: Dict[str, int] = {k: 0 for k in _COUNTED}
        self._job_label = ""
        self._job_started: Optional[float] = None
        self._idle_timer = self._make_idle_timer()
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # log first (it opens upwards, above the always-visible line)
        self._log = LogViewer(self)
        self._log.setFixedHeight(self.LOG_H)
        self._log.setVisible(False)
        self._log.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {T.BG_INPUT}; color: {T.TEXT_MUTED};"
            f" border: none; border-top: 1px solid {T.BORDER}; padding: 4px; font-size: 11px; }}"
        )
        col.addWidget(self._log)

        line = QWidget(self)
        row = QHBoxLayout(line)
        row.setContentsMargins(T.PAD_LG, 6, T.PAD_LG, 6)
        row.setSpacing(T.PAD_SM)

        self._badge = StatusBadge("never")
        row.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._action = QLabel("Ready — connect to the other computer to begin.")
        self._action.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px; border: none;")
        row.addWidget(self._action, 0, Qt.AlignmentFlag.AlignVCenter)

        self._detail = QLabel("")
        self._detail.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; border: none;")
        row.addWidget(self._detail, 0, Qt.AlignmentFlag.AlignVCenter)

        self._bar = ProgressBar(T.ACCENT, self)
        self._bar.setVisible(False)
        row.addWidget(self._bar, 1)

        self._fold_btn = GhostButton("log ▴", self, command=self.toggle_log)
        # Scoped style: the shared GhostButton padding needs 34 px of height, and a
        # fixed 22 px cut the label in half (measured, not guessed).
        self._fold_btn.setStyleSheet(
            f"QPushButton {{ color: {T.TEXT_MUTED}; background: transparent;"
            f" border: 1px solid {T.BORDER}; border-radius: 6px; padding: 1px 9px;"
            f" font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: {T.BG_HOVER}; color: {T.TEXT}; }}"
        )
        attach_tooltip(self._fold_btn, "Show or hide the event log above this bar.")
        row.addWidget(self._fold_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        col.addWidget(line)

    # ------------------------------------------------------------------
    # Public API — identical to StatusPopup
    # ------------------------------------------------------------------

    def _make_idle_timer(self) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._hide_when_idle)
        return timer

    def _wake(self) -> None:
        """Activity: cancel any pending auto-hide and make sure the bar is shown."""
        self._idle_timer.stop()
        if not self.isVisible():
            self.show()

    def _schedule_hide(self, ok: bool) -> None:
        self._idle_timer.start(_IDLE_HIDE_MS if ok else _IDLE_HIDE_ERROR_MS)

    def _hide_when_idle(self) -> None:
        """Fold the bar away — unless a job has started in the meantime."""
        if self._badge._status == "running":
            return
        self._expanded = False
        self._log.setVisible(False)
        self._fold_btn.setText("log ▴")
        self.hide()

    def begin_job(self, label: str, compare_only: bool = False) -> None:
        self._wake()
        self._job_label = label
        self._job_started = time.monotonic()
        self._counts = {k: 0 for k in _COUNTED}
        verb = "Comparing" if compare_only else "Syncing"
        self._append(f"— {verb}: {label} —", "ts")
        self._badge.set_status("running")
        self._set_action(f"Starting {label} …", "info")
        self._paint_bar(T.ACCENT)
        self._bar.setVisible(True)
        self._bar.start_indeterminate()
        self._refresh_detail()

    def set_status(self, text: str, kind: str = "info", progress: Optional[float] = None,
                   busy: bool = False) -> None:
        self._wake()
        if kind in self._counts:
            self._counts[kind] += 1
        self._append(text, kind)
        self._set_action(text, kind)
        self._bar.setVisible(True)
        if kind in ("success", "error", "connected"):
            # A plain status such as "Connected to …" is not a running job: settle
            # the bar (green / red, static) instead of leaving the marquee running
            # for ever, which read as "still working" long after connecting.
            self._schedule_hide(kind != "error")
            self._badge.set_status(kind)
            self._paint_bar(T.ERROR if kind == "error" else T.SUCCESS)
            self._bar.set_determinate(1.0)
        elif progress is not None and progress > 0:
            self._bar.set_determinate(min(progress, 1.0))
        elif busy:
            self._paint_bar(T.ACCENT)
            self._bar.start_indeterminate()
        if busy:
            self._badge.set_status("running")
        self._refresh_detail()

    def finish(self, ok: bool, message: str) -> None:
        self._wake()
        self._schedule_hide(ok)
        self._badge.set_status("success" if ok else "error")
        kind = "success" if ok else "error"
        headline = completion_headline(ok, message)
        self._set_action(headline, kind)
        self._append(headline, kind)
        # the footer answers "what was done": counters, then how long it took
        self._detail.setText(completion_footer(ok, self._counters_text(),
                                               getattr(self, "_job_started", None)))
        self._bar.setVisible(True)
        if ok:
            self._paint_bar(T.SUCCESS)
            self._bar.set_determinate(1.0)
        else:
            self._paint_bar(T.ERROR)
            self._bar.stop_indeterminate()
            self._bar.setValue(max(0, self._bar.value()))

    def on_sync_event(self, event) -> None:
        kind = getattr(event, "kind", "info")
        message = getattr(event, "message", "")
        progress = float(getattr(event, "progress", 0.0) or 0.0)
        if kind == "info" and message.startswith("Starting:"):
            self.begin_job(message.split("Starting:", 1)[1].strip())
            return
        if kind in ("success", "error", "warning"):
            if kind == "warning":
                self._counts["skip"] = self._counts.get("skip", 0) + 1
                self._append(message, kind)
                self._set_action(message, kind)
                self._refresh_detail()
            else:
                self.finish(kind == "success", message)
            return
        self.set_status(message, kind, progress=progress, busy=(progress <= 0))

    def toggle(self) -> None:
        """The panel's Activity button: bring the bar back, then show/hide the log."""
        if not self.isVisible():
            self._wake()
            if not self._expanded:
                self.toggle_log()
            return
        self.toggle_log()

    def toggle_log(self) -> None:
        self._expanded = not self._expanded
        self._log.setVisible(self._expanded)
        self._fold_btn.setText("log ▾" if self._expanded else "log ▴")

    def reveal(self) -> None:
        """Show the bar (and keep it up: a manual reveal is not auto-hidden)."""
        self._idle_timer.stop()
        self.show()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _set_action(self, text: str, kind: str) -> None:
        self._action.setText(text)
        self._action.setStyleSheet(
            f"color: {_KIND_COLORS.get(kind, T.TEXT)}; font-size: 12px; border: none;"
        )

    def _append(self, text: str, kind: str) -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{stamp}]  {text}", tag=kind if kind in T.LOG_COLORS else "info")

    def _paint_bar(self, color: str) -> None:
        self._bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INPUT}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}"
        )

    def _counters_text(self) -> str:
        parts = []
        for key, label in (("compare", "compared"), ("copy", "copied"),
                           ("delete", "deleted"), ("skip", "skipped"), ("error", "errors")):
            n = self._counts.get(key, 0)
            if n:
                parts.append(f"{n} {label}")
        return "  ·  ".join(parts)

    def _refresh_detail(self) -> None:
        self._detail.setText(self._counters_text())
