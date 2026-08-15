"""
Design tokens + QSS stylesheet for the PyQt6 UI.

Keeps the same dark-glass aesthetic as the original customtkinter theme
(accent #3b82f6) but expressed for Qt widgets.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Colour palette (identical to the original customtkinter theme)
# ---------------------------------------------------------------------------

BG_ROOT       = "#080c14"   # window / outermost background
BG_SIDEBAR    = "#0d1421"   # sidebar background
BG_PANEL      = "#111827"   # content area panels
BG_CARD       = "#1a2035"   # cards / inner containers
BG_INPUT      = "#0f1623"   # text-entry / combobox background
BG_HOVER      = "#1f2d4a"   # hover highlight

BORDER        = "#1e2d45"   # subtle border
BORDER_BRIGHT = "#2a4070"   # focused / active border

ACCENT        = "#3b82f6"   # primary accent – blue
ACCENT_HOVER  = "#2563eb"
ACCENT_DIM    = "#1d4ed8"
ACCENT2       = "#8b5cf6"   # secondary – purple
ACCENT3       = "#14b8a6"   # tertiary  – teal

TEXT          = "#e2e8f0"   # primary text
TEXT_MUTED    = "#94a3b8"   # secondary / hint text
TEXT_DIM      = "#475569"   # disabled text

SUCCESS       = "#22c55e"
WARNING       = "#f59e0b"
ERROR         = "#ef4444"
INFO          = "#38bdf8"

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

RADIUS_SM  = 6
RADIUS_MD  = 10
RADIUS_LG  = 14
RADIUS_XL  = 18

PAD_XS = 4
PAD_SM = 8
PAD_MD = 14
PAD_LG = 20
PAD_XL = 28

SIDEBAR_W  = 220
HEADER_H   = 52
CARD_GAP   = 14

# ---------------------------------------------------------------------------
# Status colour / label maps
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    "never":     TEXT_DIM,
    "success":   SUCCESS,
    "error":     ERROR,
    "running":   ACCENT,
    "cancelled": WARNING,
}

STATUS_BG = {
    "never":     "#3d4450",
    "success":   "#14532d",
    "error":     "#450a0a",
    "running":   "#1e3a5f",
    "cancelled": "#451a03",
}

STATUS_LABELS = {
    "never":     "Never synced",
    "success":   "Last sync OK",
    "error":     "Sync error",
    "running":   "Running…",
    "cancelled": "Cancelled",
}

LOG_COLORS = {
    "info":    TEXT_MUTED,
    "compare": ACCENT,
    "copy":    INFO,
    "delete":  WARNING,
    "skip":    TEXT_DIM,
    "error":   ERROR,
    "success": SUCCESS,
    "warning": WARNING,
    "ts":      TEXT_DIM,
}

# ---------------------------------------------------------------------------
# QSS stylesheet
# ---------------------------------------------------------------------------

def build_qss(light: bool = False) -> str:
    """Return the global QSS string for the app.

    ``light`` toggles a few surfaces (kept dark-first for the glass look).
    """
    bg_root   = BG_ROOT
    bg_side   = BG_SIDEBAR
    bg_panel  = BG_PANEL
    bg_card   = BG_CARD
    bg_input  = BG_INPUT
    bg_hover  = BG_HOVER
    border    = BORDER
    border_hi = BORDER_BRIGHT
    text      = TEXT
    muted     = TEXT_MUTED
    dim       = TEXT_DIM
    accent    = ACCENT
    accent_hi = ACCENT_HOVER

    if light:
        bg_root   = "#eef2f7"
        bg_side   = "#e2e8f0"
        bg_panel  = "#f8fafc"
        bg_card   = "#ffffff"
        bg_input  = "#f1f5f9"
        bg_hover  = "#dbe4f0"
        border    = "#cbd5e1"
        border_hi = "#93c5fd"
        text      = "#0f172a"
        muted     = "#475569"
        dim       = "#94a3b8"

    return f"""
    QMainWindow, QDialog {{
        background-color: {bg_root};
    }}
    QWidget {{
        color: {text};
        font-size: 13px;
        font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
    }}
    /* ── Sidebar ─────────────────────────────────────────── */
    QFrame#Sidebar {{
        background-color: {bg_side};
        border: none;
    }}
    QLabel#Logo {{
        color: {accent};
        font-size: 26px;
        font-weight: 700;
    }}
    QLabel#LogoSub {{
        color: {text};
        font-size: 20px;
        font-weight: 700;
    }}
    QLabel#SidebarTag {{
        color: {dim};
        font-size: 10px;
    }}
    /* ── Header / titles ─────────────────────────────────── */
    QLabel#PageTitle {{
        color: {text};
        font-size: 18px;
        font-weight: 700;
    }}
    QLabel#SectionTitle {{
        color: {muted};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1px;
    }}
    QLabel {{ color: {text}; }}
    QLabel[muted="true"] {{ color: {muted}; }}
    QLabel[dim="true"] {{ color: {dim}; }}
    /* ── Nav buttons ─────────────────────────────────────── */
    QPushButton#NavButton {{
        background: transparent;
        color: {muted};
        border: 1px solid transparent;
        border-radius: {RADIUS_MD}px;
        padding: 9px 12px;
        text-align: left;
        font-size: 13px;
    }}
    QPushButton#NavButton:hover {{
        background: {bg_hover};
        color: {text};
    }}
    QPushButton#NavButton:checked {{
        background: {bg_hover};
        color: {text};
        border: 1px solid {accent};
    }}
    /* ── Generic buttons ─────────────────────────────────── */
    QPushButton {{
        background-color: {bg_input};
        color: {text};
        border: 1px solid {border};
        border-radius: {RADIUS_MD}px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background-color: {bg_hover}; }}
    QPushButton:pressed {{ background-color: {border_hi}; }}
    QPushButton:disabled {{ color: {dim}; border-color: {border}; background-color: {bg_input}; }}
    QPushButton#PrimaryButton {{
        background-color: {accent};
        color: #ffffff;
        border: none;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{ background-color: {accent_hi}; }}
    QPushButton#DangerButton {{
        background-color: #450a0a;
        color: {ERROR};
        border: 1px solid {ERROR};
    }}
    QPushButton#DangerButton:hover {{ background-color: #7f1d1d; }}
    QPushButton#GhostButton {{
        background-color: transparent;
        color: {muted};
        border: 1px solid {border};
    }}
    QPushButton#GhostButton:hover {{ background-color: {bg_hover}; color: {text}; }}
    QPushButton#IconButton {{
        background-color: transparent;
        color: {muted};
        border: 1px solid {border};
        border-radius: {RADIUS_SM}px;
        padding: 4px 8px;
        font-size: 13px;
    }}
    QPushButton#IconButton:hover {{ background-color: {bg_hover}; color: {text}; }}
    /* ── Context menus ──────────────────────────────────── */
    QMenu {{
        background-color: {bg_card};
        border: 1px solid {border};
        border-radius: {RADIUS_MD}px;
        padding: 6px;
    }}
    QMenu::item {{
        background: transparent;
        color: {text};
        padding: 7px 14px;
        border-radius: {RADIUS_SM}px;
    }}
    QMenu::item:selected {{ background-color: {bg_hover}; }}
    QMenu::item:disabled {{ color: {dim}; }}
    QMenu::separator {{
        height: 1px;
        background: {border};
        margin: 4px 8px;
    }}
    /* ── Cards ───────────────────────────────────────────── */
    QFrame#GlassCard {{
        background-color: {bg_card};
        border: 1px solid {border};
        border-radius: {RADIUS_LG}px;
    }}
    QFrame#GlassCard[hovered="true"] {{
        border: 2px solid {accent};
    }}
    QFrame#CardContent {{ background: transparent; border: none; }}
    /* ── Inputs ──────────────────────────────────────────── */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
        background-color: {bg_input};
        border: 1px solid {border};
        border-radius: {RADIUS_SM}px;
        padding: 6px 8px;
        selection-background-color: {accent};
        selection-color: #ffffff;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
        border: 1px solid {accent};
    }}
    QComboBox {{
        background-color: {bg_input};
        border: 1px solid {border};
        border-radius: {RADIUS_SM}px;
        padding: 6px 8px;
    }}
    QComboBox:hover {{ border: 1px solid {border_hi}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background-color: {bg_card};
        border: 1px solid {border};
        selection-background-color: {accent};
        selection-color: #ffffff;
        outline: none;
    }}
    QCheckBox {{
        spacing: 8px;
        color: {text};
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 4px;
        border: 1px solid {border_hi};
        background-color: {bg_input};
    }}
    QCheckBox::indicator:checked {{
        background-color: {accent};
        border: 1px solid {accent};
    }}
    QRadioButton {{ spacing: 8px; color: {text}; }}
    QRadioButton::indicator {{
        width: 16px; height: 16px;
        border-radius: 8px;
        border: 1px solid {border_hi};
        background-color: {bg_input};
    }}
    QRadioButton::indicator:checked {{
        background-color: {accent};
        border: 1px solid {accent};
    }}
    /* ── Scroll areas / bars ─────────────────────────────── */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {border};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {border_hi}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {border};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {border_hi}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    /* ── Log viewer ──────────────────────────────────────── */
    QPlainTextEdit#LogViewer {{
        background-color: {bg_root};
        color: {muted};
        border: 1px solid {border};
        border-radius: {RADIUS_MD}px;
        font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
        font-size: 12px;
    }}
    /* ── Progress bar ────────────────────────────────────── */
    QProgressBar {{
        background-color: {bg_input};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: 3px;
    }}
    /* ── Tooltip ─────────────────────────────────────────── */
    QToolTip {{
        background-color: {bg_card};
        color: {text};
        border: 1px solid {border_hi};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12px;
    }}
    /* ── Tab widget (profile editor) ─────────────────────── */
    QTabWidget::pane {{
        background-color: {bg_card};
        border: 1px solid {border};
        border-radius: {RADIUS_LG}px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {muted};
        padding: 8px 16px;
        border-top-left-radius: {RADIUS_SM}px;
        border-top-right-radius: {RADIUS_SM}px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {bg_card};
        color: {text};
        border: 1px solid {border};
        border-bottom: 2px solid {accent};
    }}
    QTabBar::tab:hover:!selected {{ color: {text}; }}
    /* ── Splitter / list ─────────────────────────────────── */
    QListWidget {{
        background-color: {bg_input};
        border: 1px solid {border};
        border-radius: {RADIUS_SM}px;
    }}
    QListWidget::item {{ padding: 4px 8px; }}
    QListWidget::item:selected {{ background-color: {accent}; color: #ffffff; }}
    QToolBar {{ background: transparent; border: none; }}
    """
