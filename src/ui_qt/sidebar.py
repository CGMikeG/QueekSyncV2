"""
Sidebar navigation panel (PyQt6).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt import theme as T

NAV_ITEMS: List[Tuple[str, str, str]] = [
    ("peer",      "⇄  Peer Sync", "Sync folders between two computers over SSH"),
    ("settings",  "⚙  Settings",  "Application preferences"),
]


class RailButton(QPushButton):
    """Icon-only navigation button. Glyphs are painted, not font characters.

    The compact build needs a 64 px rail, so the two nav items carry no text —
    each glyph is drawn with QPainter (two opposed arrows for Peer Sync, three
    sliders for Settings) so it stays crisp at any scale and needs no icon files.
    """

    def __init__(self, page_id: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_id = page_id
        self._hover = False
        self.setCheckable(True)
        self.setFixedSize(40, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self.setStyleSheet("QPushButton { background: transparent; border: 0; }")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.isChecked() or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.BG_HOVER))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 9, 9)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(self.rect().adjusted(0, 9, -(self.width() - 3), -9), 1.5, 1.5)

        color = QColor(T.ACCENT if self.isChecked() else (T.TEXT if self._hover else T.TEXT_MUTED))
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self._page_id == "peer":
            # → on top, ← below: two computers exchanging files
            p.drawLine(QPointF(cx - 7, cy - 4), QPointF(cx + 7, cy - 4))
            p.drawLine(QPointF(cx + 7, cy - 4), QPointF(cx + 2, cy - 7.5))
            p.drawLine(QPointF(cx + 7, cy - 4), QPointF(cx + 2, cy - 0.5))
            p.drawLine(QPointF(cx + 7, cy + 4), QPointF(cx - 7, cy + 4))
            p.drawLine(QPointF(cx - 7, cy + 4), QPointF(cx - 2, cy + 0.5))
            p.drawLine(QPointF(cx - 7, cy + 4), QPointF(cx - 2, cy + 7.5))
        else:
            # three sliders
            for idx, offset in enumerate((-5, 0, 5)):
                p.drawLine(QPointF(cx - 7, cy + offset), QPointF(cx + 7, cy + offset))
                knob = cx - 3 if idx == 0 else (cx + 3 if idx == 1 else cx - 1)
                p.setBrush(color)
                p.drawEllipse(QPointF(knob, cy + offset), 2.0, 2.0)
                p.setBrush(Qt.BrushStyle.NoBrush)


class Sidebar(QFrame):
    def __init__(self, on_navigate: Callable[[str], None], parent: QWidget | None = None,
                 compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._compact = compact
        self.setFixedWidth(64 if compact else T.SIDEBAR_W)

        self._on_navigate = on_navigate
        self._nav_btns: Dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8 if compact else 12, 12 if compact else 18,
                                  8 if compact else 12, 12 if compact else 14)
        layout.setSpacing(6 if compact else 4)

        # ── Logo / branding ────────────────────────────────────────────
        if compact:
            logo = QLabel("Q")
            logo.setObjectName("Logo")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setStyleSheet(f"color: {T.ACCENT2}; font-size: 15px; font-weight: 800;")
            layout.addWidget(logo)
            layout.addSpacing(8)
        else:
            logo_row = QHBoxLayout()
            logo_row.setSpacing(4)
            logo = QLabel("Queek")
            logo.setObjectName("Logo")
            logo_sub = QLabel("Sync")
            logo_sub.setObjectName("LogoSub")
            logo_row.addWidget(logo)
            logo_row.addWidget(logo_sub)
            logo_row.addStretch()
            layout.addLayout(logo_row)
            layout.addSpacing(14)

        # ── Navigation buttons ─────────────────────────────────────────
        group = QButtonGroup(self)
        group.setExclusive(True)

        for page_id, label, tip in NAV_ITEMS:
            if compact:
                # icon rail: no text, the glyph is painted and the label lives in the tooltip
                btn = RailButton(page_id, f"{label.strip()} — {tip}", self)
                layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
            else:
                btn = QPushButton(label, self)
                btn.setObjectName("NavButton")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(f"Open {label.split(maxsplit=1)[-1]}. Example: {tip}.")
                layout.addWidget(btn)
            btn.clicked.connect(lambda _=False, p=page_id: self._click(p))
            group.addButton(btn)
            self._nav_btns[page_id] = btn

        layout.addStretch()

        # ── Bottom tag ─────────────────────────────────────────────────
        if not compact:
            tag = QLabel("Cross-platform file sync")
            tag.setObjectName("SidebarTag")
            tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(tag)

    # ------------------------------------------------------------------

    def _click(self, page_id: str) -> None:
        self._on_navigate(page_id)

    def set_active(self, page_id: str) -> None:
        btn = self._nav_btns.get(page_id)
        if btn is not None:
            btn.setChecked(True)
