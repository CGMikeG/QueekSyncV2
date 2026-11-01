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
    """Navigation panel — starts as an icon rail, expands to labelled buttons.

    The user asked for the left panel to be minimised to icons, so both builds
    open collapsed: a 64 px rail of painted RailButtons. The full build also gets
    a chevron that expands it to the original labelled buttons, because the labels
    are the only place the page names are spelled out.
    """

    COLLAPSED_W = 64

    def __init__(self, on_navigate: Callable[[str], None], parent: QWidget | None = None,
                 compact: bool = False, collapsed: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._compact = compact
        self._collapsed = bool(collapsed or compact)   # the compact rail never expands
        self._on_navigate = on_navigate

        self._rail_btns: Dict[str, RailButton] = {}
        self._rail_rows: Dict[str, QWidget] = {}
        self._text_btns: Dict[str, QPushButton] = {}

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(6)

        self._logo = QLabel("Q")
        self._logo.setObjectName("Logo")
        self._layout.addWidget(self._logo)
        self._layout.addSpacing(8)

        rail_group = QButtonGroup(self)
        rail_group.setExclusive(True)
        text_group = QButtonGroup(self)
        text_group.setExclusive(True)

        for page_id, label, tip in NAV_ITEMS:
            # ── icon row (collapsed) ───────────────────────────────────
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            row_layout.addStretch()
            rail = RailButton(page_id, f"{label.strip()} — {tip}", row)
            row_layout.addWidget(rail, 0, Qt.AlignmentFlag.AlignCenter)
            row_layout.addStretch()
            self._layout.addWidget(row)
            rail.clicked.connect(lambda _=False, p=page_id: self._click(p))
            rail_group.addButton(rail)
            self._rail_btns[page_id] = rail
            self._rail_rows[page_id] = row

            # ── labelled button (expanded) ─────────────────────────────
            text_btn = QPushButton(label, self)
            text_btn.setObjectName("NavButton")
            text_btn.setCheckable(True)
            text_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            text_btn.setToolTip(f"Open {label.split(maxsplit=1)[-1]}. Example: {tip}.")
            text_btn.clicked.connect(lambda _=False, p=page_id: self._click(p))
            text_group.addButton(text_btn)
            self._layout.addWidget(text_btn)
            self._text_btns[page_id] = text_btn

        self._layout.addStretch()

        # ── expand / minimise ──────────────────────────────────────────
        self._toggle_btn = QPushButton("»", self)
        self._toggle_btn.setObjectName("SidebarToggle")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(30, 24)
        # `padding: 0` is load-bearing: the global QPushButton rule pads 9px 12px,
        # which inside a 30 px button leaves no room for the glyph at all.
        self._toggle_btn.setStyleSheet(
            f"QPushButton#SidebarToggle {{ color: {T.TEXT_MUTED}; background: transparent;"
            f" border: 1px solid {T.BORDER}; border-radius: 6px; font-size: 13px;"
            f" padding: 0; }}"
            f"QPushButton#SidebarToggle:hover {{ background-color: {T.BG_HOVER};"
            f" color: {T.TEXT}; border-color: {T.ACCENT}; }}"
        )
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        self._layout.addWidget(self._toggle_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self._toggle_btn.setVisible(not compact)      # nothing to expand in the compact rail

        self._tag = QLabel("Cross-platform file sync")
        self._tag.setObjectName("SidebarTag")
        self._tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._tag)

        self._apply_state()

    # ------------------------------------------------------------------

    def _click(self, page_id: str) -> None:
        self._on_navigate(page_id)

    def set_active(self, page_id: str) -> None:
        for buttons in (self._rail_btns, self._text_btns):
            btn = buttons.get(page_id)
            if btn is not None:
                btn.setChecked(True)

    def toggle_collapsed(self) -> None:
        """Chevron: swap between the icon rail and the labelled panel."""
        if self._compact:
            return
        self._collapsed = not self._collapsed
        self._apply_state()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def _apply_state(self) -> None:
        self.setFixedWidth(self.COLLAPSED_W if self._collapsed else T.SIDEBAR_W)
        side = 8 if self._collapsed else 12
        self._layout.setContentsMargins(side, 12 if self._collapsed else 18,
                                        side, 12 if self._collapsed else 14)
        self._layout.setSpacing(6 if self._collapsed else 4)

        if self._collapsed:
            self._logo.setText("Q")
            self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._logo.setStyleSheet(
                f"color: {T.ACCENT2}; font-size: 15px; font-weight: 800; border: none;")
        else:
            self._logo.setText("QueekSync")
            self._logo.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._logo.setStyleSheet(
                f"color: {T.ACCENT}; font-size: 20px; font-weight: 700; border: none;")

        for row in self._rail_rows.values():
            row.setVisible(self._collapsed)
        for btn in self._text_btns.values():
            btn.setVisible(not self._collapsed)
        self._tag.setVisible(not self._collapsed)
        self._toggle_btn.setText("»" if self._collapsed else "«")
        self._toggle_btn.setToolTip("Show the menu labels" if self._collapsed
                                    else "Minimise the menu to icons")
