"""
Sidebar navigation panel (PyQt6).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from PyQt6.QtCore import Qt
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
    ("dashboard", "◈  Dashboard", "Overview of all profiles and activity"),
    ("peer",      "⇄  Peer Sync", "Sync folders between two computers over SSH"),
    ("profiles",  "☰  Profiles",  "Manage sync profiles"),
    ("monitor",   "◉  Monitor",   "Live sync progress and logs"),
    ("settings",  "⚙  Settings",  "Application preferences"),
]


class Sidebar(QFrame):
    def __init__(self, on_navigate: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T.SIDEBAR_W)

        self._on_navigate = on_navigate
        self._nav_btns: Dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 18, 12, 14)
        layout.setSpacing(4)

        # ── Logo / branding ────────────────────────────────────────────
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
            btn = QPushButton(label, self)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Open {label.split(maxsplit=1)[-1]}. Example: {tip}.")
            btn.clicked.connect(lambda _=False, p=page_id: self._click(p))
            group.addButton(btn)
            layout.addWidget(btn)
            self._nav_btns[page_id] = btn

        layout.addStretch()

        # ── Bottom tag ─────────────────────────────────────────────────
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
