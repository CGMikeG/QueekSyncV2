"""
Floating activity window — replaces the removed Monitor page.

One place that answers "what is the app doing right now": the current action,
a progress bar, running counters, and a colour-tagged feed of recent events.

Wired from QueekSyncApp::

    app.status_popup.begin_job("Peer: Docs")            # a sync/compare starts
    app.status_popup.on_sync_event(event)               # every engine event
    app.report_activity("Comparing folders …", "compare", busy=True)
    app.status_popup.toggle()                          # user button / Activity

It is a frameless child of the main window (so it travels with the window and
never lands in the taskbar), anchored bottom-right, draggable by its body.
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ui_qt import theme as T
from ui_qt.widgets import (
    HSeparator,
    IconButton,
    LogViewer,
    MutedLabel,
    ProgressBar,
    StatusBadge,
    attach_tooltip,
)

# Event kind -> colour of the "current action" line (log tags reuse T.LOG_COLORS)
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

# Counters kept on the "so far" line
_COUNTED = ("compare", "copy", "delete", "skip", "error")


class StatusPopup(QFrame):
    """Small always-available status window that pops over the app content."""

    WIDTH = 470
    LOG_H = 150
    MARGIN = 18

    def __init__(self, app: QWidget) -> None:
        super().__init__(app)
        self._app = app
        self.setObjectName("StatusPopup")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(
            f"QFrame#StatusPopup {{ background-color: {T.BG_CARD};"
            f" border: 1px solid {T.BORDER_BRIGHT}; border-radius: {T.RADIUS_MD}px; }}"
        )

        self._expanded = True
        self._drag_from: Optional[QPoint] = None
        self._counts: Dict[str, int] = {}

        self._build()
        self.hide()
        app.installEventFilter(self)   # keep the anchor on window resize

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAD_MD, T.PAD_SM, T.PAD_MD, T.PAD_MD)
        root.setSpacing(T.PAD_SM)

        # ── header: dot · title · state badge · collapse · close ────────
        head = QHBoxLayout()
        head.setSpacing(T.PAD_SM)

        self._dot = QLabel("◆")
        self._dot.setStyleSheet(f"color: {T.ACCENT2}; font-size: 12px; border: none;")
        head.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)

        title = QLabel("Activity")
        title.setStyleSheet(f"color: {T.TEXT}; font-size: 12px; font-weight: 700; border: none;")
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        self._badge = StatusBadge("never")
        head.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch()

        self._fold_btn = IconButton("▼", self, command=self.toggle_log)
        attach_tooltip(self._fold_btn, "Show or hide the event list. The current action stays visible.")
        head.addWidget(self._fold_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        close_btn = IconButton("×", self, command=self.hide)
        attach_tooltip(close_btn, "Hide this window. Click 'Activity' in Peer Sync to bring it back.")
        head.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        # ── what it is doing now ───────────────────────────────────────
        self._action = QLabel("Idle — nothing is running.")
        self._action.setWordWrap(True)
        self._action.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px; border: none;")
        root.addWidget(self._action)

        self._bar = ProgressBar(T.ACCENT, self)
        root.addWidget(self._bar)

        self._detail = MutedLabel("")
        self._detail.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 11px; border: none;")
        root.addWidget(self._detail)

        # ── event feed (collapsible) ───────────────────────────────────
        self._sep = HSeparator(self)
        root.addWidget(self._sep)

        self._log = LogViewer(self)
        self._log.setFixedHeight(self.LOG_H)
        self._log.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {T.BG_INPUT}; color: {T.TEXT_MUTED};"
            f" border: 1px solid {T.BORDER}; border-radius: {T.RADIUS_SM}px;"
            f" padding: 4px; font-size: 11px; }}"
        )
        root.addWidget(self._log)
        self.adjustSize()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_job(self, label: str, compare_only: bool = False) -> None:
        """A sync/compare just started: reset counters, show, go busy."""
        self._counts = {k: 0 for k in _COUNTED}
        verb = "Comparing" if compare_only else "Syncing"
        self._append(f"— {verb}: {label} —", "ts")
        self._badge.set_status("running")
        self._set_action(f"Starting {label} …", "info")
        self._paint_bar(T.ACCENT)
        self._bar.start_indeterminate()
        self._refresh_detail()
        self.reveal()

    def set_status(self, text: str, kind: str = "info", progress: Optional[float] = None,
                   busy: bool = False) -> None:
        """Set the current action line and record the event in the feed."""
        if kind in self._counts:
            self._counts[kind] += 1
        self._append(text, kind)
        self._set_action(text, kind)
        if progress is not None and progress > 0:
            self._bar.set_determinate(min(progress, 1.0))
        elif busy:
            self._bar.start_indeterminate()
        if busy:
            self._badge.set_status("running")
        self._refresh_detail()
        if not self.isVisible():
            self.reveal()

    def finish(self, ok: bool, message: str) -> None:
        """A job ended: stop the bar, set the final state."""
        self._badge.set_status("success" if ok else "error")
        self._set_action(message, "success" if ok else "error")
        self._append(message, "success" if ok else "error")
        if ok:
            self._paint_bar(T.SUCCESS)
            self._bar.set_determinate(1.0)
        else:
            self._paint_bar(T.ERROR)
            self._bar.stop_indeterminate()
            self._bar.setValue(max(0, self._bar.value()))   # no -1 from the marquee
        self._refresh_detail()

    def on_sync_event(self, event) -> None:
        """Feed a SyncEvent from the app's event pump."""
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
                self.reveal()
            else:
                self.finish(kind == "success", message)
            return
        self.set_status(message, kind, progress=progress, busy=(progress <= 0))

    def toggle(self) -> None:
        """Show/hide the window (the Activity button)."""
        if self.isVisible():
            self.hide()
        else:
            self.reveal()

    def reveal(self) -> None:
        """Show and anchor bottom-right of the main window."""
        self._reposition()
        self.show()
        self.raise_()

    def toggle_log(self) -> None:
        """Fold/unfold the event list, keeping the current action visible."""
        self._expanded = not self._expanded
        self._log.setVisible(self._expanded)
        self._sep.setVisible(self._expanded)
        self._fold_btn.setText("▼" if self._expanded else "▶")
        self.adjustSize()
        self._reposition()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _paint_bar(self, color: str) -> None:
        """Recolour the progress chunk: accent = running, green ok, red failed."""
        self._bar.setStyleSheet(
            f"QProgressBar {{ background-color: {T.BG_INPUT}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 3px; }}"
        )

    def _set_action(self, text: str, kind: str) -> None:
        color = _KIND_COLORS.get(kind, T.TEXT)
        self._action.setText(text)
        self._action.setStyleSheet(f"color: {color}; font-size: 12px; border: none;")

    def _append(self, text: str, kind: str) -> None:
        stamp = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        self._log.append(f"[{stamp}]  {text}", tag=kind if kind in T.LOG_COLORS else "info")

    def _refresh_detail(self) -> None:
        parts = []
        for key, label in (("compare", "compared"), ("copy", "copied"),
                           ("delete", "deleted"), ("skip", "skipped"), ("error", "errors")):
            n = self._counts.get(key, 0)
            if n:
                parts.append(f"{n} {label}")
        self._detail.setText("  ·  ".join(parts))

    def _reposition(self) -> None:
        parent = self._app
        if parent is None:
            return
        self.adjustSize()
        area = parent.rect()
        x = max(0, area.width() - self.width() - self.MARGIN)
        y = max(0, area.height() - self.height() - self.MARGIN)
        self.move(x, y)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if obj is self._app and event.type() == QEvent.Type.Resize and self.isVisible():
            self._reposition()
        return super().eventFilter(obj, event)

    # ── drag the window by its body ────────────────────────────────────
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_from is None:
            return
        area = self._app.rect()
        target = self.mapToParent(event.position().toPoint()) - self._drag_from
        x = min(max(0, target.x()), max(0, area.width() - self.width()))
        y = min(max(0, target.y()), max(0, area.height() - self.height()))
        self.move(x, y)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_from = None
