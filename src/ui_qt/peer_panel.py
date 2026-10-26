
"""
Peer Sync panel – sync folders between two computers over SSH (PyQt6).

Flow:
  1. Connect to the other computer (IP, username, password).
  2. Favourite and sync-list folders on either machine are pinned to the top
     of the lists; the other computer's sync list can be synced in one click.
  3. Tick folders to sync, Compare to see which side is newer, Sync to run.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.peer import (
    PairCompare,
    PeerConnection,
    PeerConnectionConfig,
    PeerPlace,
    PeerPlan,
    add_local_favorite,
    add_remote_favorite,
    add_to_sync_list,
    compare_pair,
    QUICK_COMPARE_LIMIT,
    quick_compare,
    delete_peer_connection,
    find_or_create_peer_profile,
    list_local_folders,
    load_local_favorites,
    load_local_sync_list,
    load_peer_connections,
    read_remote_favorites,
    read_remote_sync_list,
    remote_places,
    remove_local_favorite,
    remove_remote_favorite,
    remove_from_sync_list,
    rename_peer_connection,
    save_local_favorites,
    save_peer_connections,
    touch_peer_connection,
    upsert_peer_connection,
)
from ui_qt import theme as T
from ui_qt.widgets import (
    GhostButton,
    GlassCard,
    IconButton,
    LabelledEntry,
    MutedLabel,
    PrimaryButton,
    ScrollArea,
    SectionLabel,
    attach_tooltip,
)

if TYPE_CHECKING:
    from ui_qt.app import QueekSyncApp

_VERDICT_LABELS = {
    "in_sync":      "In sync",
    "local_newer":  "This PC newer",
    "remote_newer": "Other PC newer",
    "mixed":        "Both changed",
    "only_local":   "Only on this PC",
    "only_remote":  "Only on other PC",
    "empty":        "Empty folder",
    "error":        "Error",
}

_VERDICT_COLORS = {
    "in_sync":      T.SUCCESS,
    "local_newer":  T.ACCENT,
    "remote_newer": T.ACCENT2,
    "mixed":        T.WARNING,
    "only_local":   T.INFO,
    "only_remote":  T.INFO,
    "empty":        T.TEXT_DIM,
    "error":        T.ERROR,
}

# Row data roles
_R_KEY   = Qt.ItemDataRole.UserRole      # unique key
_R_NAME  = Qt.ItemDataRole.UserRole + 1  # display name (basename)
_R_MTIME = Qt.ItemDataRole.UserRole + 2  # last-modified time
_R_KIND  = Qt.ItemDataRole.UserRole + 3  # "dir" | "fav" | "sync"
_R_PATH  = Qt.ItemDataRole.UserRole + 4  # absolute path
_R_CONN  = Qt.ItemDataRole.UserRole + 5  # saved connection name


class _PeerSignals(QObject):
    """Thread-safe signal bridge for background workers."""

    connected = pyqtSignal(bool, str)            # ok, message
    folders_loaded = pyqtSignal(str, list)       # side, entries
    favorites_loaded = pyqtSignal(str, list)     # side, favourite paths
    sync_list_loaded = pyqtSignal(str, list)     # side, sync-list paths
    compare_row = pyqtSignal(str, str, str)      # key, side, status
    pair_compared = pyqtSignal(str, object)      # key, PairCompare
    safety_checked = pyqtSignal(list, object)    # [(PeerPlan, PairCompare)], ctx dict
    sync_done = pyqtSignal(bool, str)


def _fmt_mtime(mtime: float) -> str:
    if not mtime:
        return "never"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _fmt_mtime_short(mtime: float) -> str:
    """Compact date for table cells: '26 Oct 16:24', with the year only when needed."""
    if not mtime:
        return "never"
    when = datetime.fromtimestamp(mtime)
    if when.year == datetime.now().year:
        return when.strftime("%d %b %H:%M")
    return when.strftime("%d %b %Y")


def _human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


def _norm(p: str) -> str:
    return os.path.normpath(p)


class SyncSafetyDialog(QDialog):
    """Pre-sync safety check: shows what each folder holds (file counts,
    sizes, content types, latest update) plus the differences between the
    two sides, and asks the user to confirm before the sync proceeds."""

    def __init__(self, parent, results, ctx) -> None:
        super().__init__(parent)
        self.setWindowTitle("Check folders before syncing")
        self.setModal(True)
        self.resize(680, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAD_LG, T.PAD_LG, T.PAD_LG, T.PAD_LG)
        root.setSpacing(T.PAD_MD)

        title = QLabel("Check folders before syncing")
        title.setStyleSheet(f"color: {T.TEXT}; font-size: 17px; font-weight: 700;")
        root.addWidget(title)

        subtitle = MutedLabel(
            "These folders don't match exactly. Review what each side holds, "
            "then decide whether to sync them anyway."
        )
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(380)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(2, 2, 2, 2)
        inner_layout.setSpacing(T.PAD_SM)
        for plan, cmp in results:
            inner_layout.addWidget(self._build_section(plan, cmp))
        inner_layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        extra = self._extra_text(ctx)
        if extra:
            extra_lbl = MutedLabel(extra)
            extra_lbl.setWordWrap(True)
            root.addWidget(extra_lbl)

        btn_row = QHBoxLayout()
        cancel_btn = GhostButton("Cancel", self, command=self.reject)
        cancel_btn.setDefault(True)
        sync_btn = PrimaryButton("▶  Sync anyway", self, command=self.accept)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(sync_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _build_section(self, plan, cmp) -> GlassCard:
        card = GlassCard(self)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        lay.setSpacing(6)

        head = QLabel(f"📁 {plan.name}")
        head.setStyleSheet(f"color: {T.TEXT}; font-size: 14px; font-weight: 700;")
        lay.addWidget(head)

        for side_lbl, scan in (("This PC", cmp.local), ("Other PC", cmp.remote)):
            lay.addWidget(self._side_label(side_lbl, scan))

        diffs = cmp.difference_lines()
        if diffs:
            for line in diffs:
                warn = QLabel(f"⚠  {line}")
                warn.setStyleSheet(f"color: {T.WARNING}; font-size: 12px;")
                warn.setWordWrap(True)
                lay.addWidget(warn)
        else:
            ok = QLabel("✓  Folders match on both sides")
            ok.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
            lay.addWidget(ok)
        return card

    def _side_label(self, side_lbl: str, scan) -> QLabel:
        if scan.missing:
            txt, color = f"{side_lbl}:  not present", T.WARNING
        elif scan.error:
            txt, color = f"{side_lbl}:  error: {scan.error}", T.ERROR
        else:
            txt = (
                f"{side_lbl}:  {scan.path}\n"
                f"    {scan.file_count} file(s) · {_human_size(scan.total_size)}"
                f" · last updated {_fmt_mtime(scan.latest_mtime)}\n"
                f"    contents: {scan.content_summary()}"
            )
            color = T.TEXT
        lbl = QLabel(txt)
        lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
        lbl.setWordWrap(True)
        return lbl

    def _extra_text(self, ctx) -> str:
        parts = []
        direction_label = ctx.get("direction_label", "")
        if direction_label:
            parts.append(f"Direction: {direction_label}")
        if ctx.get("delete_extra"):
            parts.append("Extra files at the destination will be deleted (mirror).")
        skipped = ctx.get("skipped") or []
        if skipped:
            parts.append(f"Skipped (only on one side): {', '.join(skipped)}")
        return "  ·  ".join(parts)


class RemoteFolderDialog(QDialog):
    """Pick a base folder on the other computer by browsing its drives.

    Lists the other computer's places - its home folder, the filesystem root
    and every mounted volume (extra/internal media drives) - plus the folders
    inside the current one, so a folder on another drive can be chosen without
    typing its path.
    """

    _R_PLACE = Qt.ItemDataRole.UserRole

    def __init__(self, parent, peer: PeerConnection, start_path: str = "") -> None:
        super().__init__(parent)
        self._peer = peer
        self._path = (start_path or peer.home_dir or "/").rstrip("/") or "/"
        self.chosen_path = ""

        self.setWindowTitle("Choose a folder on the other computer")
        self.setModal(True)
        self.resize(640, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(T.PAD_LG, T.PAD_LG, T.PAD_LG, T.PAD_LG)
        root.setSpacing(T.PAD_SM)

        title = QLabel("Choose a base folder on the other computer")
        title.setStyleSheet(f"color: {T.TEXT}; font-size: 17px; font-weight: 700;")
        root.addWidget(title)

        hint = MutedLabel(
            f"{peer.username}@{peer.host} — pick one of its drives below, then open the "
            "folder you want to sync."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addWidget(SectionLabel("Drives and places on the other computer"))
        self._places = QListWidget()
        self._places.setMaximumHeight(140)
        self._places.itemClicked.connect(self._on_place_chosen)
        self._places.itemActivated.connect(self._on_place_chosen)
        self._places.setStyleSheet(self._list_style())
        attach_tooltip(self._places, "Mounted drives found on the other computer, plus its "
                                     "home folder and the filesystem root.")
        root.addWidget(self._places)

        root.addWidget(SectionLabel("Folders here"))
        path_row = QHBoxLayout()
        self._up_btn = GhostButton("↑  Up", self, command=self._go_up)
        self._up_btn.setFixedSize(80, 30)
        path_row.addWidget(self._up_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._path_lbl = QLabel(self._path)
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_lbl.setStyleSheet(f"color: {T.ACCENT}; font-size: 12px; font-weight: 600;")
        path_row.addWidget(self._path_lbl, 1)
        root.addLayout(path_row)

        self._folders = QListWidget()
        self._folders.itemActivated.connect(self._on_folder_chosen)
        self._folders.setStyleSheet(self._list_style())
        attach_tooltip(self._folders, "Double-click a folder to open it.")
        root.addWidget(self._folders, 1)

        self._status = MutedLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel = GhostButton("Cancel", self, command=self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        use_btn = PrimaryButton("Use this folder", self, command=self._use_this_folder)
        btn_row.addWidget(use_btn)
        root.addLayout(btn_row)
        attach_tooltip(use_btn, "Use the folder shown above as the other computer's base folder.")

        self._load_places()
        self._show_folder(self._path)

    # ------------------------------------------------------------------

    @staticmethod
    def _list_style() -> str:
        return (
            f"QListWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {T.BG_HOVER};"
            f" border: 1px solid {T.ACCENT}; }}"
        )

    def _load_places(self) -> None:
        """Fill the places list with the other computer's drives and roots."""
        self._places.clear()
        try:
            places: List[PeerPlace] = remote_places(self._peer)
        except Exception as exc:
            places = []
            self._set_status(f"Could not read the other computer's drives: {exc}", T.ERROR)
        for place in places:
            if place.kind == "drive":
                text = f"{place.label}   ·   {place.path}"
            elif place.kind == "home":
                text = f"Home folder   ·   {place.path}"
            else:
                text = f"File system   ·   {place.path}"
            item = QListWidgetItem(text)
            item.setData(self._R_PLACE, place.path)
            item.setToolTip(f"Open {place.path}")
            self._places.addItem(item)

    def _on_place_chosen(self, item: QListWidgetItem) -> None:
        path = item.data(self._R_PLACE)
        if path:
            self._show_folder(path)

    def _on_folder_chosen(self, item: QListWidgetItem) -> None:
        self._show_folder(f"{self._path.rstrip('/')}/{item.text()}")

    def _go_up(self) -> None:
        if self._path in ("", "/"):
            return
        self._show_folder(self._path.rsplit("/", 1)[0] or "/")

    def _show_folder(self, path: str) -> None:
        """List the folders inside ``path`` on the other computer."""
        path = path.rstrip("/") or "/"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            names = [f["name"] for f in self._peer.list_folders(path)]
            error = ""
        except Exception as exc:
            names, error = [], str(exc)
        finally:
            QApplication.restoreOverrideCursor()

        self._path = path
        self._path_lbl.setText(path)
        self._up_btn.setEnabled(path != "/")
        self._folders.clear()
        for name in names:
            self._folders.addItem(QListWidgetItem(name))

        if error:
            self._set_status(f"Could not open {path}: {error}", T.ERROR)
        elif not names:
            self._set_status("No folders inside this one - you can still use it as the base folder.",
                             T.TEXT_MUTED)
        else:
            self._set_status(f"{len(names)} folder(s). Double-click one to open it, or click "
                             "'Use this folder'.", T.TEXT_MUTED)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _use_this_folder(self) -> None:
        self.chosen_path = self._path
        self.accept()


# The three steps the panel walks through, in order.
_STEPS: List[Tuple[str, str]] = [
    ("connect", "Connect"),
    ("folders", "Folders"),
    ("review", "Compare && sync"),   # && = a literal ampersand in Qt labels
]


class StepChip(QPushButton):
    """A step in the panel's header: number + label, three states.

    Painted by the stylesheet rather than with icons, and left-aligned so the
    three chips read as a sequence: idle (muted), current (highlighted), done
    (green tick once that step has been completed).
    """

    def __init__(self, number: str, label: str, parent: Optional[QWidget] = None,
                 command: Optional[Callable] = None) -> None:
        super().__init__(parent)
        self._number = number
        self._label = label
        self._state = "idle"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        if command is not None:
            self.clicked.connect(command)
        self._restyle()

    def set_state(self, state: str) -> None:
        self._state = state
        self._restyle()

    def _restyle(self) -> None:
        if self._state == "done":
            self.setText(f"✓  {self._label}")
            color, bg, border = T.SUCCESS, f"{T.SUCCESS}22", f"{T.SUCCESS}55"
        elif self._state == "current":
            self.setText(f"{self._number}  {self._label}")
            color, bg, border = T.TEXT, T.BG_HOVER, T.BORDER_BRIGHT
        else:
            self.setText(f"{self._number}  {self._label}")
            color, bg, border = T.TEXT_MUTED, "transparent", T.BORDER
        self.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 2px 10px; border-radius: 7px;"
            f" color: {color}; background-color: {bg}; border: 1px solid {border};"
            f" font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {T.BG_HOVER}; color: {T.TEXT}; }}"
        )


class PeerSyncPanel(QWidget):
    """Side-by-side folder selection and sync between two computers."""

    def __init__(self, app: "QueekSyncApp", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        if app is None:
            raise ValueError(
                "PeerSyncPanel needs the QueekSyncApp window: Sync reads "
                "app.profile_mgr / app.save_profile / app.start_sync. Build it through "
                "QueekSyncApp() and select the 'peer' page instead of passing None "
                "(a None app aborts the process later, inside the Sync slot)."
            )
        self._app = app
        self._peer: Optional[PeerConnection] = None
        self._signals = _PeerSignals()
        self._connections: List[PeerConnectionConfig] = load_peer_connections()

        # loaded state per side; None = not loaded yet
        self._folders: Dict[str, Optional[List[dict]]] = {"local": None, "remote": None}
        self._favs: Dict[str, Optional[List[str]]] = {"local": None, "remote": None}
        self._sync_lists: Dict[str, Optional[List[str]]] = {"local": None, "remote": None}
        self._checked: Dict[str, bool] = {}

        self._signals.connected.connect(self._on_connected)
        self._signals.folders_loaded.connect(self._on_folders_loaded)
        self._signals.favorites_loaded.connect(self._on_favorites_loaded)
        self._signals.sync_list_loaded.connect(self._on_sync_list_loaded)
        self._signals.compare_row.connect(self._on_compare_row)
        self._signals.pair_compared.connect(self._on_pair_compared)
        self._signals.safety_checked.connect(self._on_safety_checked)
        self._signals.sync_done.connect(self._on_sync_done)

        self._build()

    # ==================================================================
    # Layout
    # ==================================================================

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── step bar: 1 Connect › 2 Folders › 3 Compare & sync ──────────
        # One compact strip replaces the four collapsible section headers and
        # carries the connection status, so the important line never scrolls
        # away and the steps read as a sequence instead of four loose cards.
        stepbar = QWidget(self)
        stepbar.setStyleSheet(
            f"background-color: {T.BG_PANEL}; border-bottom: 1px solid {T.BORDER};"
        )
        stepbar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        step_row = QHBoxLayout(stepbar)
        step_row.setContentsMargins(T.PAD_LG, T.PAD_SM, T.PAD_LG, T.PAD_SM)
        step_row.setSpacing(T.PAD_SM)

        self._step_chips: Dict[str, StepChip] = {}
        for idx, (key, label) in enumerate(_STEPS):
            if idx:
                sep = QLabel("›")
                sep.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 13px; border: none;")
                step_row.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
            # clicked(bool) passes `checked` into the handler: the leading `_` must
            # be there or it silently overwrites the captured step key.
            chip = StepChip(str(idx + 1), label, stepbar,
                            command=lambda _=False, k=key: self._toggle_step(k))
            step_row.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            self._step_chips[key] = chip

        step_row.addStretch()
        self._status_lbl = MutedLabel("Connect to the other computer to begin.")
        step_row.addWidget(self._status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self._activity_btn = GhostButton("◆  Activity", stepbar, command=self._toggle_activity)
        step_row.addWidget(self._activity_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        attach_tooltip(self._activity_btn,
                       "Show or hide the activity log pinned at the bottom of the window: "
                       "what the app is doing now, with progress and recent events.")
        root.addWidget(stepbar)

        # A vertical splitter rather than a scroll area: the folder list and the
        # results table share the height, and the divider between them can be
        # dragged. Its handle stays visible (the styled 7 px bar) so it is findable.
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setObjectName("StepSplitter")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(7)
        self._splitter.setStyleSheet(
            f"QSplitter#StepSplitter {{ background-color: {T.BG_ROOT}; }}"
            f"QSplitter#StepSplitter::handle {{ background-color: {T.BG_PANEL}; }}"
            f"QSplitter#StepSplitter::handle:hover {{ background-color: {T.ACCENT}; }}"
            f"QSplitter#StepSplitter::handle:pressed {{ background-color: {T.ACCENT_HOVER}; }}"
        )
        self._splitter.setContentsMargins(T.PAD_LG, T.PAD_MD, T.PAD_LG, T.PAD_LG)
        host = self          # cards are reparented into the splitter below
        root.addWidget(self._splitter, 1)   # absorbs the leftover height

        # ── step 1 · Connect — saved connections beside the form ───────
        conn_card = GlassCard(host)
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        conn_layout.setSpacing(T.PAD_SM)

        conn_cols = QHBoxLayout()
        conn_cols.setSpacing(T.PAD_LG)

        saved_col = QVBoxLayout()
        saved_col.setSpacing(6)
        saved_col.addWidget(SectionLabel("Saved connections"))
        self._conn_list = QListWidget(conn_card)
        self._conn_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._conn_list.setMinimumHeight(84)
        self._conn_list.setMaximumHeight(96)      # was a 300 px panel of its own
        self._conn_list.currentRowChanged.connect(self._on_conn_selected)
        self._conn_list.itemDoubleClicked.connect(self._load_connection)
        self._conn_list.setStyleSheet(
            f"QListWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 5px 8px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {T.BG_HOVER};"
            f" border: 1px solid {T.ACCENT}; }}"
        )
        saved_col.addWidget(self._conn_list)
        attach_tooltip(self._conn_list,
                       "Select a saved SSH connection and click Load to connect, or fill in the "
                       "details on the right and click Save. Click New first to add another.")

        saved_btn_row = QHBoxLayout()
        saved_btn_row.setSpacing(6)
        self._load_conn_btn = PrimaryButton("Load", conn_card, command=self._load_connection)
        saved_btn_row.addWidget(self._load_conn_btn)
        new_conn_btn = GhostButton("New", conn_card, command=self._new_connection)
        saved_btn_row.addWidget(new_conn_btn)
        save_conn_btn = GhostButton("Save", conn_card, command=self._save_connection)
        saved_btn_row.addWidget(save_conn_btn)
        self._rename_conn_btn = GhostButton("Rename", conn_card, command=self._rename_connection)
        saved_btn_row.addWidget(self._rename_conn_btn)
        self._delete_conn_btn = GhostButton("Delete", conn_card, command=self._delete_connection)
        saved_btn_row.addWidget(self._delete_conn_btn)
        saved_btn_row.addStretch()
        saved_col.addLayout(saved_btn_row)
        attach_tooltip(new_conn_btn,
                       "Start a new connection: clears the fields so the next Save adds a separate "
                       "connection instead of updating the selected one.")
        attach_tooltip(save_conn_btn,
                       "Save the name and details. A new name adds a connection; keeping the loaded "
                       "connection's name updates it.")
        attach_tooltip(self._rename_conn_btn, "Rename the selected saved connection.")
        self._conn_hint = MutedLabel("")
        self._conn_hint.setWordWrap(True)
        saved_col.addWidget(self._conn_hint)
        saved_col.addStretch()
        conn_cols.addLayout(saved_col, 2)

        form_col = QVBoxLayout()
        form_col.setSpacing(T.PAD_SM)
        form_col.addWidget(SectionLabel("Connection details"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(T.PAD_SM)
        grid.setVerticalSpacing(T.PAD_SM)
        self._name_entry = LabelledEntry("Connection name", placeholder="e.g. MSI laptop")
        self._host_entry = LabelledEntry("IP address or hostname", placeholder="192.168.1.50")
        self._port_entry = LabelledEntry("SSH port", placeholder="22")
        self._port_entry.set("22")
        self._user_entry = LabelledEntry("Username")
        self._pass_entry = LabelledEntry("Password", show="●")
        grid.addWidget(self._name_entry, 0, 0, 1, 2)
        grid.addWidget(self._host_entry, 1, 0)
        grid.addWidget(self._port_entry, 1, 1)
        grid.addWidget(self._user_entry, 2, 0)
        grid.addWidget(self._pass_entry, 2, 1)
        form_col.addLayout(grid)

        conn_row = QHBoxLayout()
        self._remember_cb = QCheckBox("Remember password")
        self._remember_cb.setChecked(True)
        conn_row.addWidget(self._remember_cb)
        attach_tooltip(self._remember_cb,
                       "Store the password with the saved connection. Stored in plaintext in the "
                       "app config folder, like SFTP profile passwords.")
        self._connect_btn = PrimaryButton("Connect", conn_card, command=self._connect)
        conn_row.addWidget(self._connect_btn)
        self._disconnect_btn = GhostButton("Disconnect", conn_card, command=self._disconnect)
        self._disconnect_btn.setEnabled(False)
        conn_row.addWidget(self._disconnect_btn)
        conn_row.addStretch()
        self._conn_detail = MutedLabel("")
        conn_row.addWidget(self._conn_detail)
        form_col.addLayout(conn_row)
        form_col.addStretch()
        conn_cols.addLayout(form_col, 3)

        conn_layout.addLayout(conn_cols)   # (added to the splitter below)

        # ── step 2 · Folders — both computers side by side ─────────────
        root_card = GlassCard(host)
        root_layout = QVBoxLayout(root_card)
        root_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        root_layout.setSpacing(T.PAD_SM)

        roots_row = QHBoxLayout()
        roots_row.setSpacing(T.PAD_SM)
        self._local_root = LabelledEntry("This computer – base folder",
                                        placeholder=str(os.path.expanduser("~")))
        self._local_root.set(os.path.expanduser("~"))
        roots_row.addWidget(self._local_root, 1)
        browse_btn = GhostButton("Browse…", root_card, command=self._browse_local_root)
        browse_btn.setFixedSize(90, 34)
        roots_row.addWidget(browse_btn, 0, Qt.AlignmentFlag.AlignBottom)
        fav_btn = IconButton("★", root_card, command=self._toggle_favorite)
        roots_row.addWidget(fav_btn, 0, Qt.AlignmentFlag.AlignBottom)
        sync_list_btn = IconButton("⇄", root_card, command=self._toggle_sync_list)
        roots_row.addWidget(sync_list_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self._remote_root = LabelledEntry("Other computer – base folder", placeholder="/home/user")
        roots_row.addWidget(self._remote_root, 1)
        self._remote_browse_btn = GhostButton("Browse…", root_card, command=self._browse_remote_root)
        self._remote_browse_btn.setFixedSize(90, 34)
        self._remote_browse_btn.setEnabled(False)
        roots_row.addWidget(self._remote_browse_btn, 0, Qt.AlignmentFlag.AlignBottom)
        refresh_btn = IconButton("⟳", root_card, command=self._reload_folders)
        roots_row.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignBottom)
        root_layout.addLayout(roots_row)
        attach_tooltip(fav_btn, "Add (or remove) this computer's base folder as a favourite. "
                               "The other computer sees it when it connects.")
        attach_tooltip(sync_list_btn, "Add (or remove) this computer's base folder to the sync list. "
                                     "The other computer can then sync it with one click.")
        attach_tooltip(refresh_btn, "Re-read both folder lists from disk.")

        lists_row = QHBoxLayout()
        lists_row.setSpacing(T.PAD_MD)
        self._local_list, self._local_header = self._make_folder_list(
            "This PC  (★ favourite · ⇄ sync list)", lists_row)
        self._remote_list, self._remote_header = self._make_folder_list(
            "Other PC  (★ favourite · ⇄ sync list)", lists_row)
        root_layout.addLayout(lists_row, 1)

        # ── step 3 · Compare & sync ────────────────────────────────────
        plan_card = GlassCard(host)
        plan_layout = QVBoxLayout(plan_card)
        plan_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        plan_layout.setSpacing(T.PAD_SM)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(T.PAD_SM)
        compare_btn = GhostButton("≋  Compare", plan_card, command=self._compare_selected)
        actions_row.addWidget(compare_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._quick_btn = GhostButton("»  Quick compare", plan_card,
                                      command=self._quick_compare_selected)
        actions_row.addWidget(self._quick_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._fav_sync_btn = GhostButton("★  Favourites", plan_card, command=self._sync_favorites)
        actions_row.addWidget(self._fav_sync_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_remote_btn = GhostButton("⇄  Remote list", plan_card,
                                            command=self._sync_remote_list)
        actions_row.addWidget(self._sync_remote_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        actions_row.addStretch()
        self._sync_btn = PrimaryButton("▶  Sync selected", plan_card, command=self._sync_selected)
        actions_row.addWidget(self._sync_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        plan_layout.addLayout(actions_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(T.PAD_SM)
        settings_label = MutedLabel("Sync direction")
        settings_row.addWidget(settings_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._direction_combo = QComboBox(plan_card)
        self._direction_combo.addItem("Two-way (automatic)", "auto")
        self._direction_combo.addItem("This PC → Other PC", "local_to_remote")
        self._direction_combo.addItem("Other PC → This PC", "remote_to_local")
        settings_row.addWidget(self._direction_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        self._delete_extra_cb = QCheckBox("Delete extra files")
        self._delete_extra_cb.setEnabled(False)
        settings_row.addWidget(self._delete_extra_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        self._copy_missing_cb = QCheckBox("Copy one-sided folders")
        self._copy_missing_cb.setChecked(True)
        settings_row.addWidget(self._copy_missing_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        settings_row.addStretch()
        plan_layout.addLayout(settings_row)

        attach_tooltip(compare_btn,
                       "Scan the selected folders on both computers, show which side is newer, and "
                       "report the date/time each folder's content was last updated.")
        attach_tooltip(self._quick_btn,
                       f"Quick compare: check only the newest {QUICK_COMPARE_LIMIT} files in each "
                       "folder instead of every file, so a big folder answers 'which computer has "
                       "the newer work?' in about a second. Use Compare for a file-by-file verdict.")
        attach_tooltip(self._sync_btn, "Sync the selected folder pairs (two-way when both sides exist).")
        attach_tooltip(self._fav_sync_btn, "Select every favourite folder on both computers and sync them.")
        attach_tooltip(self._sync_remote_btn,
                       "Download the other computer's sync list and sync those folders.")
        attach_tooltip(self._direction_combo,
                       "Two-way (automatic): folders on both computers sync both ways, folders on one "
                       "side only are copied across. Pick a one-way direction to always copy that way.")
        attach_tooltip(self._delete_extra_cb,
                       "Delete extra files at the destination: when a one-way direction is chosen, also "
                       "delete files there that no longer exist in the source (mirror sync).")
        attach_tooltip(self._copy_missing_cb,
                       "Copy one-sided folders: folders checked on one side only are copied across.")
        self._direction_combo.currentIndexChanged.connect(self._update_direction_ui)

        self._plan_table = QTableWidget(0, 5)
        self._plan_table.setHorizontalHeaderLabels(
            ["Folder", "This PC newest", "Other PC newest", "Verdict", "Action"]
        )
        self._plan_table.verticalHeader().setVisible(False)
        self._plan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._plan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        plan_header = self._plan_table.horizontalHeader()
        plan_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        plan_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        plan_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        plan_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        plan_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._plan_table.setMinimumHeight(88)   # draggable: the splitter sets the height
        self._plan_table.setStyleSheet(
            f"QTableWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; gridline-color: {T.BORDER}; }}"
            f"QHeaderView::section {{ background-color: {T.BG_CARD}; color: {T.TEXT_MUTED};"
            f" border: none; padding: 6px; font-weight: 600; }}"
            f"QTableWidget::item {{ padding: 5px 8px; }}"
            f"QTableWidget::item:selected {{ background-color: {T.BG_HOVER}; }}"
        )
        plan_layout.addWidget(self._plan_table, 1)

        self._plan_status = MutedLabel("Tick folders on either side, then Compare or Sync.")
        plan_layout.addWidget(self._plan_status)

        # Steps can stay open together (folders + results is the working view), so the
        # window fills with content instead of one card stretched over a big void.
        # Only the folder lists and the results table grow; the form is fixed content.
        self._step_cards = {"connect": conn_card, "folders": root_card, "review": plan_card}
        for key, card in self._step_cards.items():
            # GlassCard reports a Minimum vertical policy, so Qt used its sizeHint
            # (e.g. 332 px) as the splitter floor and left no room to drag. An
            # explicit floor plus Expanding gives the divider real travel.
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            # floors keep every card's own controls visible: the form is fixed
            # content, the results card needs its action + settings + status rows
            card.setMinimumHeight({"connect": 270, "folders": 130, "review": 240}[key])
            self._splitter.addWidget(card)
        # the form keeps its natural height; the two working cards share the rest
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 1)
        self._splitter.setSizes([210, 330, 330])

        self._open_steps: set = {"connect", "folders"}   # shows the flow at launch
        self._apply_steps()
        self._set_connected_ui(False)
        self._populate_connections()

        # Show this computer's folders straight away — they need no connection — and
        # let the other side's list say why it is empty until you connect.
        try:
            self._folders["local"] = list_local_folders(self._local_root_text())
        except Exception:
            self._folders["local"] = []
        self._render_lists()

    # ==================================================================
    # Step bar (accordion)
    # ==================================================================

    def _toggle_step(self, key: str) -> None:
        """Show or hide one step; the others keep their state."""
        if key in self._open_steps:
            self._open_steps.discard(key)
        else:
            self._open_steps.add(key)
        self._apply_steps()

    def _open_step_now(self, key: str) -> None:
        """Reveal a step without hiding the rest (used when the flow advances).

        Once connected the connect card is finished with, so advancing past it
        puts it away — otherwise three cards stack up and the panel scrolls.
        """
        if self._peer is not None and key != "connect":
            self._open_steps.discard("connect")
        self._open_steps.add(key)
        self._apply_steps()

    def _show_only_step(self, *keys: str) -> None:
        """Replace the visible steps (e.g. connect collapses once connected)."""
        self._open_steps = set(keys)
        self._apply_steps()

    def _apply_steps(self) -> None:
        """Show every open step and colour the chips to match."""
        connected = self._peer is not None
        for key, card in self._step_cards.items():
            card.setVisible(key in self._open_steps)
        for key, chip in self._step_chips.items():
            if key in self._open_steps:
                chip.set_state("current")
            elif key == "connect" and connected:
                chip.set_state("done")
            else:
                chip.set_state("idle")

    def _make_folder_list(self, title: str, parent_layout: QHBoxLayout) -> tuple:
        col = QWidget()
        col_layout = QVBoxLayout(col)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(4)
        header_row = QHBoxLayout()
        header = MutedLabel(title)
        header.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px; font-weight: 600;")
        header_row.addWidget(header)
        header_row.addStretch()
        col_layout.addLayout(header_row)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lst.setMinimumHeight(88)    # a small floor only: the splitter decides the rest
        lst.itemChanged.connect(self._on_item_changed)
        lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lst.customContextMenuRequested.connect(self._on_list_context_menu)
        attach_tooltip(lst, "Right-click a folder to add it to favourites (★); right-click a ★ "
                            "favourite to remove it. On the other computer's list the favourite "
                            "is saved on that computer.")
        lst.setStyleSheet(
            f"QListWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 4px 2px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {T.BG_HOVER}; }}"
        )
        col_layout.addWidget(lst, 1)
        parent_layout.addWidget(col, 1)
        return lst, header

    # ==================================================================
    # Connection
    # ==================================================================

    def _toggle_activity(self) -> None:
        """Show/hide the floating activity window."""
        popup = getattr(self._app, "status_popup", None)
        if popup is not None:
            popup.toggle()

    def _report(self, text: str, kind: str = "info", busy: bool = False,
                done: Optional[bool] = None) -> None:
        """Tell the activity window what this panel is doing (no-op without an app).

        ``done`` closes the running job (True/False = succeeded/failed). Panel-driven
        compares go through this, so without it the window sits on "Running…" for
        ever — only engine events would ever finish a job.
        """
        popup = getattr(self._app, "status_popup", None)
        if popup is None:
            return
        if done is not None:
            popup.finish(done, text)
            return
        reporter = getattr(self._app, "report_activity", None)
        if callable(reporter):
            reporter(text, kind, busy=busy)

    def _connect(self) -> None:
        host = self._host_entry.get().strip()
        user = self._user_entry.get().strip()
        pw = self._pass_entry.get()
        if not host or not user:
            QMessageBox.warning(self, "Peer Sync", "Enter the other computer's IP address and username first.")
            return
        try:
            port = int(self._port_entry.get() or 22)
        except ValueError:
            port = 22

        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("Connecting…")
        self._status_lbl.setText(f"Connecting to {user}@{host}:{port} …")
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")
        self._report(f"Connecting to {user}@{host}:{port} …", "info", busy=True)

        def _try() -> None:
            peer = PeerConnection(host, port, user, pw)
            try:
                home = peer.connect()
                self._peer = peer
                self._signals.connected.emit(True, home)
            except Exception as exc:
                try:
                    peer.disconnect()
                except Exception:
                    pass
                self._signals.connected.emit(False, str(exc))

        threading.Thread(target=_try, daemon=True).start()

    # ==================================================================
    # Saved connections
    # ==================================================================

    def _populate_connections(self, select_name: Optional[str] = None) -> None:
        """Rebuild the saved-connection list, keeping the selection where possible.

        ``select_name`` picks the connection to select (used after a save or
        rename); otherwise the previously selected connection stays selected.
        """
        if select_name is None:
            row = self._conn_list.currentRow()
            previous = self._connections or []
            if 0 <= row < len(previous):
                select_name = previous[row].name

        self._connections = load_peer_connections()
        self._conn_list.blockSignals(True)
        self._conn_list.clear()
        for c in self._connections:
            item = QListWidgetItem(self._conn_label(c))
            item.setData(_R_CONN, c.name)
            item.setToolTip(f"Load {c.name} ({c.username}@{c.host}:{c.port})")
            self._conn_list.addItem(item)
        self._conn_list.blockSignals(False)
        has_connections = bool(self._connections)
        self._load_conn_btn.setEnabled(has_connections)
        self._rename_conn_btn.setEnabled(has_connections)
        self._delete_conn_btn.setEnabled(has_connections)
        if has_connections:
            index = next(
                (i for i, c in enumerate(self._connections) if c.name == select_name), 0
            )
            self._conn_list.setCurrentRow(index)
            self._fill_connection_fields(self._connections[index])
            self._conn_hint.setText("Select a connection and click Load to connect to it.")
        else:
            self._conn_list.setCurrentRow(-1)
            self._conn_hint.setText("No saved connections yet - enter the details below, "
                                    "then click 'Save'.")

    @staticmethod
    def _conn_label(c: PeerConnectionConfig) -> str:
        return f"{c.name}  ({c.username}@{c.host}:{c.port})"

    def _fill_connection_fields(self, c: PeerConnectionConfig) -> None:
        self._name_entry.set(c.name)
        self._host_entry.set(c.host)
        self._port_entry.set(str(c.port))
        self._user_entry.set(c.username)
        self._pass_entry.set(c.password if c.remember_password else "")
        self._remember_cb.setChecked(c.remember_password)

    def _selected_connection_name(self) -> str:
        """Name of the saved connection highlighted in the list ("" when none)."""
        row = self._conn_list.currentRow()
        if 0 <= row < len(self._connections):
            return self._connections[row].name
        return ""

    def _on_conn_selected(self, index: int) -> None:
        if index < 0 or index >= len(self._connections):
            return
        self._fill_connection_fields(self._connections[index])

    def _load_connection(self) -> None:
        """Load the selected saved connection: fill the fields and connect."""
        row = self._conn_list.currentRow()
        if row < 0 or row >= len(self._connections):
            QMessageBox.information(self, "Load Connection", "Select a saved connection first.")
            return
        conn = self._connections[row]
        self._fill_connection_fields(conn)
        self._status_lbl.setText(f"Loading saved connection '{conn.name}' ...")
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")
        self._connect()

    def _current_connection_name(self) -> str:
        """Name for a save: the Connection name field, else the selected
        connection, else user@host."""
        typed = self._name_entry.get().strip()
        if typed:
            return typed
        selected = self._selected_connection_name()
        if selected:
            return selected
        user = self._user_entry.get().strip()
        host = self._host_entry.get().strip()
        return f"{user}@{host}" if user else host

    def _new_connection(self) -> None:
        """Clear the form so the next Save adds a separate connection."""
        self._conn_list.setCurrentRow(-1)
        self._name_entry.set("")
        self._host_entry.set("")
        self._port_entry.set("22")
        self._user_entry.set("")
        self._pass_entry.set("")
        self._remember_cb.setChecked(True)
        self._conn_hint.setText("New connection - enter a name and the details below, then click "
                                "Save. Saved connections are left unchanged.")
        self._status_lbl.setText("Fill in the new connection's name and details, then click Save.")
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")

    def _rename_connection(self) -> None:
        """Rename the selected saved connection."""
        old_name = self._selected_connection_name()
        if not old_name:
            QMessageBox.information(self, "Rename Connection", "Select a saved connection first.")
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Connection", f"New name for '{old_name}':", text=old_name
        )
        new_name = (new_name or "").strip()
        if not ok or not new_name or new_name == old_name:
            return
        if any(c.name == new_name for c in self._connections):
            QMessageBox.warning(
                self, "Rename Connection", f"A connection named '{new_name}' already exists."
            )
            return
        if not rename_peer_connection(old_name, new_name):
            QMessageBox.warning(self, "Rename Connection", "Could not rename the connection.")
            return
        self._populate_connections(select_name=new_name)
        self._status_lbl.setText(f"Renamed '{old_name}' to '{new_name}'.")
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")

    def _save_connection(self) -> None:
        host = self._host_entry.get().strip()
        user = self._user_entry.get().strip()
        if not host:
            QMessageBox.warning(self, "Save Connection", "Enter the IP address or hostname first.")
            return
        try:
            port = int(self._port_entry.get() or 22)
        except ValueError:
            port = 22

        name = self._current_connection_name() or host
        selected = self._selected_connection_name()
        exists = any(c.name == name for c in self._connections)

        if exists and name != selected:
            # Saving over a different connection's name - make sure that is meant.
            if QMessageBox.question(
                self, "Save Connection",
                f"A connection named '{name}' already exists.\n\n"
                "Replace it with the details above?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        elif selected and name != selected and not exists:
            # The name no longer matches the loaded connection: offer a rename so
            # editing the name here never silently leaves a duplicate behind.
            if QMessageBox.question(
                self, "Rename Connection",
                f"Rename '{selected}' to '{name}'?\n\n"
                f"Choosing No saves these details as a separate connection instead.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) == QMessageBox.StandardButton.Yes:
                rename_peer_connection(selected, name)

        conn = PeerConnectionConfig(
            name=name or host,
            host=host,
            port=port,
            username=user,
            password=self._pass_entry.get(),
            remember_password=self._remember_cb.isChecked(),
        )
        is_new = upsert_peer_connection(conn)
        self._populate_connections(select_name=conn.name)
        self._status_lbl.setText(
            f"Saved connection '{conn.name}'. " + ("Select it in the Saved connections panel and click Load to connect." if is_new else "Updated.")
        )
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")

    def _delete_connection(self) -> None:
        row = self._conn_list.currentRow()
        if row < 0 or row >= len(self._connections):
            QMessageBox.information(self, "Delete Connection", "Select a saved connection first.")
            return
        name = self._connections[row].name
        if QMessageBox.question(
            self, "Delete Connection", f"Delete saved connection '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        delete_peer_connection(name)
        self._populate_connections()
        self._status_lbl.setText(f"Deleted connection '{name}'.")
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")

    def _on_connected(self, ok: bool, message: str) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        if not ok:
            self._status_lbl.setText(f"Connection failed: {message[:120]}")
            self._status_lbl.setStyleSheet(f"color: {T.ERROR}; font-size: 12px;")
            self._report(f"Connection failed: {message[:120]}", "error")
            return

        self._set_connected_ui(True)
        label = f"{self._peer.username}@{self._peer.host}:{self._peer.port}"
        self._remote_header.setText(f"Other PC  {self._peer.host}  (★ favourite · ⇄ sync list)")
        self._remote_root.set(message)
        self._conn_detail.setText(f"Connected · home: {message}")
        self._status_lbl.setText(f"Connected to {label}.")
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
        self._report(f"Connected to {label}.", "connected")

        # move the matching saved connection to the top (most recently used)
        match = next(
            (c for c in self._connections
             if c.host == self._peer.host and c.port == self._peer.port and c.username == self._peer.username),
            None,
        )
        if match is not None and self._connections and self._connections[0].name != match.name:
            touch_peer_connection(match.name)
            self._populate_connections()
        self._show_only_step("folders", "review")   # connected: the working view
        self._reload_folders()

    def _disconnect(self) -> None:
        if self._peer is not None:
            try:
                self._peer.disconnect()
            except Exception:
                pass
            self._peer = None
        self._set_connected_ui(False)
        self._folders = {"local": None, "remote": None}
        self._favs = {"local": None, "remote": None}
        self._sync_lists = {"local": None, "remote": None}
        self._checked = {}
        self._local_list.clear()
        self._remote_list.clear()
        self._plan_table.setRowCount(0)
        self._remote_header.setText("Folders on the other computer  (★ favourite · ⇄ sync list)")
        self._conn_detail.setText("")
        self._status_lbl.setText("Disconnected. Enter the other computer's details to reconnect.")
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")

    def _set_connected_ui(self, connected: bool) -> None:
        self._disconnect_btn.setEnabled(connected)
        self._sync_btn.setEnabled(connected)
        self._fav_sync_btn.setEnabled(connected)
        self._sync_remote_btn.setEnabled(connected)
        self._remote_browse_btn.setEnabled(connected)

    # ==================================================================
    # Folder listing + favourites
    # ==================================================================

    def _local_root_text(self) -> str:
        return os.path.expanduser(self._local_root.get().strip()) or os.path.expanduser("~")

    def _remote_root_text(self) -> str:
        text = self._remote_root.get().strip()
        if text:
            return text
        return self._peer.home_dir if self._peer is not None else ""

    def _browse_local_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select base folder on this computer")
        if path:
            self._local_root.set(path)
            self._reload_folders()

    def _browse_remote_root(self) -> None:
        """Pick a base folder on the other computer, its other drives included."""
        if self._peer is None:
            QMessageBox.information(self, "Browse", "Connect to the other computer first.")
            return
        dlg = RemoteFolderDialog(self, self._peer, self._remote_root_text())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen_path:
            self._remote_root.set(dlg.chosen_path)
            self._reload_folders()

    def _reload_folders(self) -> None:
        if self._peer is None:
            return
        local_path = self._local_root_text()
        remote_path = self._remote_root_text()
        self._local_root.set(local_path)
        self._remote_root.set(remote_path)
        self._checked = {}
        self._folders = {"local": None, "remote": None}
        self._favs = {"local": None, "remote": None}
        self._sync_lists = {"local": None, "remote": None}
        self._plan_table.setRowCount(0)

        self._status_lbl.setText("Listing folders …")
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")

        def _load() -> None:
            local_err = ""
            local_folders: List[dict] = []
            try:
                local_folders = list_local_folders(local_path)
            except Exception as exc:
                local_err = str(exc)
            self._signals.folders_loaded.emit("local", local_folders if not local_err else [])
            self._signals.favorites_loaded.emit("local", load_local_favorites())
            self._signals.sync_list_loaded.emit("local", load_local_sync_list())

            remote_err = ""
            remote_folders: List[dict] = []
            remote_favs: List[str] = []
            remote_sync: List[str] = []
            if self._peer is not None:
                try:
                    remote_folders = self._peer.list_folders(remote_path)
                except Exception as exc:
                    remote_err = str(exc)
                # these read the OTHER computer's files over SFTP: a failure here
                # must not kill the loader thread and leave the lists blank for ever
                try:
                    remote_favs = read_remote_favorites(self._peer)
                    remote_sync = read_remote_sync_list(self._peer)
                except Exception as exc:      # noqa: BLE001
                    remote_err = remote_err or f"could not read favourites: {exc}"
            self._signals.folders_loaded.emit("remote", remote_folders if not remote_err else [])
            self._signals.favorites_loaded.emit("remote", remote_favs)
            self._signals.sync_list_loaded.emit("remote", remote_sync)

            if local_err or remote_err:
                self._signals.sync_done.emit(False, (f"Could not list folders:\n"
                                                     f"  local: {local_err}\n  remote: {remote_err}"))

        threading.Thread(target=_load, daemon=True).start()

    def _on_folders_loaded(self, side: str, folders: List[dict]) -> None:
        self._folders[side] = folders
        self._maybe_render()

    def _on_favorites_loaded(self, side: str, favs: List[str]) -> None:
        self._favs[side] = [_norm(p) for p in favs]
        self._maybe_render()

    def _on_sync_list_loaded(self, side: str, paths: List[str]) -> None:
        self._sync_lists[side] = [_norm(p) for p in paths]
        self._maybe_render()

    def _maybe_render(self) -> None:
        if (
            self._folders["local"] is None or self._folders["remote"] is None
            or self._favs["local"] is None or self._favs["remote"] is None
            or self._sync_lists["local"] is None or self._sync_lists["remote"] is None
        ):
            return
        self._render_lists()
        n_local = len(self._favs["local"])
        n_remote = len(self._favs["remote"])
        n_local_sync = len(self._sync_lists["local"])
        n_remote_sync = len(self._sync_lists["remote"])
        self._status_lbl.setText(
            f"Loaded folders on both computers. "
            f"Favourites: {n_local} on this PC, {n_remote} on the other PC. "
            f"Sync lists: {n_local_sync} on this PC, {n_remote_sync} on the other PC."
        )
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
        self._plan_status.setText("Tick folders on either side, then Compare or Sync.")

    # ------------------------------------------------------------------

    def _render_lists(self) -> None:
        self._render_list(self._local_list, "local")
        self._render_list(self._remote_list, "remote")

    def _render_placeholder(self, lst: QListWidget, side: str) -> None:
        """One muted, non-selectable line so an empty list explains itself."""
        text = ("Connect to the other computer to load its folders."
                if side == "remote" and self._peer is None
                else "No folders here — check the base folder, or click ⟳.")
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)          # not selectable / not tickable
        item.setForeground(QColor(T.TEXT_DIM))
        item.setData(_R_KIND, "placeholder")
        lst.addItem(item)

    def _render_list(self, lst: QListWidget, side: str) -> None:
        root = self._local_root_text() if side == "local" else self._remote_root_text()
        favs = self._favs.get(side) or []
        syncs = self._sync_lists.get(side) or []
        folders = self._folders.get(side) or []

        rows: List[Tuple[str, str, float, str, str]] = []  # key, name, mtime, kind, path
        for p in favs:
            name = os.path.basename(p.rstrip("/\\")) or p
            rows.append((f"fav:{p}", name, 0.0, "fav", p))
        fav_norm = {_norm(p) for p in favs}
        for p in syncs:
            name = os.path.basename(p.rstrip("/\\")) or p
            rows.append((f"sync:{p}", name, 0.0, "sync", p))
        sync_norm = {_norm(p) for p in syncs}
        for f in folders:
            name = f["name"]
            path = os.path.join(root, name)
            if _norm(path) in fav_norm or _norm(path) in sync_norm:
                continue  # already shown as a favourite / sync-list row
            rows.append((f"dir:{_norm(path)}", name, f["mtime"], "dir", path))

        lst.blockSignals(True)
        lst.clear()
        for key, name, mtime, kind, path in rows:
            if kind == "fav":
                text = f"★ {name}"
            elif kind == "sync":
                text = f"⇄ {name}"
            else:
                text = f"{name}   ·   {_fmt_mtime(mtime)}"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if self._checked.get(key, False) else Qt.CheckState.Unchecked)
            item.setData(_R_KEY, key)
            item.setData(_R_NAME, name)
            item.setData(_R_MTIME, mtime)
            item.setData(_R_KIND, kind)
            item.setData(_R_PATH, path)
            if kind == "fav":
                item.setForeground(QColor(T.WARNING))
                item.setToolTip(path)
            elif kind == "sync":
                item.setForeground(QColor(T.ACCENT2))
                item.setToolTip(path + "\nOn the other computer's sync list.")
            lst.addItem(item)
        if not rows:
            self._render_placeholder(lst, side)   # never leave a blank box
        lst.blockSignals(False)

    # ==================================================================
    # Selection
    # ==================================================================

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(_R_KEY)
        if not key:
            return
        self._checked[key] = item.checkState() == Qt.CheckState.Checked
        n = sum(1 for v in self._checked.values() if v)
        self._plan_status.setText(f"{n} folder(s) selected. Use Compare to preview or Sync to run.")

    def _selected_plans(self) -> List[PeerPlan]:
        """Group checked rows into plans, pairing local/remote rows by name.

        Rows keep their absolute paths, so two favourite folders with the
        same name (one on each computer) pair into a single two-way sync
        even when their full paths differ.
        """
        plans: Dict[str, PeerPlan] = {}
        for lst, side in ((self._local_list, "local"), (self._remote_list, "remote")):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() != Qt.CheckState.Checked:
                    continue
                name = item.data(_R_NAME)
                path = item.data(_R_PATH)
                if not name or not path:
                    continue   # placeholder / stale row: it cannot describe a folder
                plan = plans.setdefault(name, PeerPlan(name=name, key=name))
                if side == "local":
                    plan.local_checked = True
                    plan.local_path = path
                else:
                    plan.remote_checked = True
                    plan.remote_path = path
        return list(plans.values())

    def _toggle_favorite(self) -> None:
        """Add (or remove) the base folder shown above as a favourite."""
        path = self._local_root_text()
        name = os.path.basename(path.rstrip("/\\")) or path
        norm = _norm(path)
        items = [p for p in (self._favs.get("local") or [])]
        if norm in [_norm(p) for p in items]:
            remove_local_favorite(path)
            self._favs["local"] = [p for p in items if _norm(p) != norm]
            self._status_lbl.setText(f"Removed '{name}' from favourites.")
        else:
            add_local_favorite(path)
            if norm not in [_norm(p) for p in items]:
                items.append(path)
            self._favs["local"] = items
            self._status_lbl.setText(
                f"Added '{name}' to favourites. The other computer will see it when it connects."
            )
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    def _toggle_sync_list(self) -> None:
        """Add (or remove) the base folder shown above to the sync list."""
        path = self._local_root_text()
        name = os.path.basename(path.rstrip("/\\")) or path
        norm = _norm(path)
        items = [p for p in (self._sync_lists.get("local") or [])]
        if norm in [_norm(p) for p in items]:
            remove_from_sync_list(path)
            self._sync_lists["local"] = [p for p in items if _norm(p) != norm]
            self._status_lbl.setText(f"Removed '{name}' from the sync list.")
        else:
            add_to_sync_list(path)
            if norm not in [_norm(p) for p in items]:
                items.append(path)
            self._sync_lists["local"] = items
            self._status_lbl.setText(
                f"Added '{name}' to the sync list. The other computer can sync it with one click."
            )
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    @staticmethod
    def _menu_style(danger: bool = False, dim: bool = False) -> str:
        """Themed QMenu stylesheet: ``danger`` = red destructive item,
        ``dim`` = greyed-out informational item, else normal menu text."""
        if danger:
            item = (f"QMenu::item {{ background: transparent; color: {T.ERROR};"
                    f" padding: 7px 14px; border-radius: {T.RADIUS_SM}px; }}"
                    f"QMenu::item:selected {{ background-color: #7f1d1d; }}")
        elif dim:
            item = (f"QMenu::item {{ background: transparent; color: {T.TEXT_DIM};"
                    f" padding: 7px 14px; border-radius: {T.RADIUS_SM}px; }}")
        else:
            item = (f"QMenu::item {{ background: transparent; color: {T.TEXT};"
                    f" padding: 7px 14px; border-radius: {T.RADIUS_SM}px; }}"
                    f"QMenu::item:selected {{ background-color: {T.BG_HOVER}; }}")
        return (f"QMenu {{ background-color: {T.BG_CARD}; border: 1px solid {T.BORDER};"
                f" border-radius: {T.RADIUS_MD}px; padding: 6px; }}" + item)

    def _on_list_context_menu(self, pos) -> None:
        """Right-click a folder row: add it to favourites, or remove an
        existing ★ favourite from them."""
        lst = self.sender()
        if not isinstance(lst, QListWidget) or (lst is not self._local_list and lst is not self._remote_list):
            return
        item = lst.itemAt(pos)
        if item is None:
            return
        path = item.data(_R_PATH)
        name = item.data(_R_NAME)
        kind = item.data(_R_KIND)

        menu = QMenu(self)
        if lst is self._remote_list:
            # A favourite for a folder on the other computer is stored in THAT
            # computer's favourites file (over SFTP), so it shows up there too
            # and stays correct when it is the one doing the syncing.
            if kind == "fav":
                menu.setStyleSheet(self._menu_style(danger=True))
                act = menu.addAction("★  Remove from the other computer's favourites")
                assert act is not None
                act.triggered.connect(lambda: self._remove_remote_favorite(path, name))
            else:
                menu.setStyleSheet(self._menu_style())
                act = menu.addAction("★  Add to the other computer's favourites")
                assert act is not None
                act.triggered.connect(lambda: self._add_remote_favorite(path, name))
        elif kind == "fav":
            # Destructive action → follow the app's DangerButton look
            # (red on dark red) instead of the default washed-out menu text.
            menu.setStyleSheet(self._menu_style(danger=True))
            act = menu.addAction("★  Remove from favourites")
            assert act is not None
            act.triggered.connect(lambda: self._remove_favorite(path, name))
        else:
            menu.setStyleSheet(self._menu_style())
            act = menu.addAction("★  Add to favourites")
            assert act is not None
            act.triggered.connect(lambda: self._add_favorite(path, name))
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _add_favorite(self, path: str, name: str) -> None:
        """Add a folder from the list to this computer's favourites."""
        norm = _norm(path)
        items = list(self._favs.get("local") or [])
        if norm in [_norm(p) for p in items]:
            return
        add_local_favorite(path)
        if norm not in [_norm(p) for p in items]:
            items.append(path)
        self._favs["local"] = items
        # Keep the row ticked if it was selected before becoming a favourite, so
        # adding a favourite never drops a folder out of the pending sync.
        if self._checked.pop(f"dir:{norm}", False) or self._checked.pop(f"sync:{path}", False):
            self._checked[f"fav:{path}"] = True
        self._status_lbl.setText(
            f"Added '{name}' to favourites. The other computer will see it when it connects."
        )
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    def _add_remote_favorite(self, path: str, name: str) -> None:
        """Add a folder on the other computer to ITS favourites, over SFTP."""
        if self._peer is None:
            return
        norm = _norm(path)
        try:
            added = add_remote_favorite(self._peer, path)
        except Exception as exc:
            QMessageBox.warning(
                self, "Favourites",
                f"Could not save the favourite on the other computer:\n\n{exc}")
            return
        if not added:
            return
        items = list(self._favs.get("remote") or [])
        if norm not in [_norm(p) for p in items]:
            items.append(path)
        self._favs["remote"] = items
        # Keep the row ticked if it was selected before becoming a favourite.
        if self._checked.pop(f"dir:{norm}", False) or self._checked.pop(f"sync:{path}", False):
            self._checked[f"fav:{path}"] = True
        self._status_lbl.setText(
            f"Added '{name}' to the other computer's favourites - saved on "
            f"{self._peer.host}, so it is there when that computer runs QueekSync too."
        )
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    def _remove_remote_favorite(self, path: str, name: str) -> None:
        """Remove a favourite from the other computer's favourites, over SFTP."""
        if self._peer is None:
            return
        norm = _norm(path)
        if QMessageBox.question(
            self, "Remove Favourite",
            f"Remove '{name}' from the other computer's favourites?\n\n{path}\n\n"
            f"({self._peer.host})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = remove_remote_favorite(self._peer, path)
        except Exception as exc:
            QMessageBox.warning(
                self, "Favourites",
                f"Could not remove the favourite on the other computer:\n\n{exc}")
            return
        if not removed:
            return
        self._favs["remote"] = [p for p in (self._favs.get("remote") or []) if _norm(p) != norm]
        for key in [k for k in self._checked if k.startswith("fav:") and _norm(k[4:]) == norm]:
            self._checked.pop(key, None)
        self._status_lbl.setText(f"Removed '{name}' from the other computer's favourites.")
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    def _remove_favorite(self, path: str, name: str) -> None:
        """Remove a favourite folder from this computer's favourites list."""
        norm = _norm(path)
        if QMessageBox.question(
            self, "Remove Favourite",
            f"Remove '{name}' from favourites?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        remove_local_favorite(path)
        items = [p for p in (self._favs.get("local") or []) if _norm(p) != norm]
        if not items:
            # Keep the file in step with the panel even if they had drifted
            # (e.g. the favourites file was missing or edited externally),
            # so a removed row can never reappear on the next reload.
            save_local_favorites([])
        self._favs["local"] = items
        for key in [k for k in self._checked if k.startswith("fav:") and _norm(k[4:]) == norm]:
            self._checked.pop(key, None)
        self._status_lbl.setText(f"Removed '{name}' from favourites.")
        self._status_lbl.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._render_lists()

    # ==================================================================
    # Compare
    # ==================================================================

    def _compare_selected(self) -> None:
        """Full compare: every file in every selected folder."""
        self._run_compare(quick=False)

    def _quick_compare_selected(self) -> None:
        """Quick compare: only the newest few files per folder (fast verdict)."""
        self._run_compare(quick=True)

    def _run_compare(self, quick: bool) -> None:
        if self._peer is None:
            return
        plans = self._selected_plans()
        if not plans:
            QMessageBox.information(self, "Compare", "Tick at least one folder on either side first.")
            return
        self._plan_table.setRowCount(0)
        for plan in plans:
            self._append_plan_row(plan.key, plan.name, "scanning", "scanning", "…", "…")
        self._open_step_now("review")

        scope = f"newest {QUICK_COMPARE_LIMIT} file(s) per folder" if quick else "every file"
        what = "Quick comparison" if quick else "Comparison"
        self._plan_status.setText(f"{what} of {len(plans)} folder(s) — {scope} …")
        self._report(f"{what} of {len(plans)} folder(s) — {scope} …", "compare", busy=True)

        local_root = self._local_root_text()
        remote_root = self._remote_root_text()

        def _run() -> None:
            # A traceback inside a worker thread used to vanish, leaving the table
            # stuck on "Checking …" — which the user reads as a frozen/crashed app.
            try:
                for idx, plan in enumerate(plans, start=1):
                    self._signals.compare_row.emit(plan.key, "local", f"Checking {idx}/{len(plans)} …")
                    local_path = plan.local_path or os.path.join(local_root, plan.name)
                    remote_path = plan.remote_path or f"{remote_root.rstrip('/')}/{plan.name}"
                    if quick:
                        cmp = quick_compare(self._peer, local_path, remote_path, plan.name,
                                            QUICK_COMPARE_LIMIT)
                    else:
                        cmp = compare_pair(self._peer, local_path, remote_path, plan.name)
                    self._signals.pair_compared.emit(plan.key, cmp)
            except Exception as exc:      # noqa: BLE001
                self._signals.sync_done.emit(False, f"{what} failed: {exc}")
                return
            self._signals.sync_done.emit(True, f"{what} finished for {len(plans)} folder(s).")

        threading.Thread(target=_run, daemon=True).start()

    def _on_compare_row(self, key: str, side: str, status: str) -> None:
        row = self._row_for_key(key)
        if row is None:
            return
        col = 1 if side == "local" else 2
        item = self._plan_table.item(row, col)
        if item is not None:
            item.setText(status)

    def _on_pair_compared(self, key: str, cmp: PairCompare) -> None:
        row = self._row_for_key(key)
        if row is None:
            return
        def _side_text(side, quick: bool) -> str:
            """One line: when that side last changed, and what changed last."""
            if side.error or side.missing:
                return side.summary_line()
            when = _fmt_mtime_short(side.latest_mtime)
            if quick:
                return f"{when}  ·  {side.latest_name or '—'}"
            return f"{when}  ·  {side.file_count} files, {_human_size(side.total_size)}"

        def _side_tooltip(side) -> str:
            if side.error or side.missing:
                return side.summary_line()
            return (f"Last updated: {_fmt_mtime(side.latest_mtime)}\n"
                    f"Newest file: {side.latest_name or '—'}\n"
                    f"{side.file_count:,} files · {_human_size(side.total_size)}")

        local_txt = _side_text(cmp.local, cmp.quick)
        remote_txt = _side_text(cmp.remote, cmp.quick)
        self._set_cell(row, 1, local_txt)
        self._set_cell(row, 2, remote_txt)
        for col, tip in ((1, _side_tooltip(cmp.local)), (2, _side_tooltip(cmp.remote))):
            cell_item = self._plan_table.item(row, col)
            if cell_item is not None:
                cell_item.setToolTip(tip)   # the cell elides; the tooltip keeps it all
        verdict_txt = _VERDICT_LABELS.get(cmp.verdict, cmp.verdict)
        verdict_color = _VERDICT_COLORS.get(cmp.verdict, T.TEXT)
        # The two date columns already say when each side changed, so the verdict
        # only names the side (colour-coded) — repeating the date here used to
        # squeeze the file-path columns into ellipses.
        newest_side = cmp.newest_side
        if newest_side == "local" and cmp.newest_mtime:
            verdict_color = T.ACCENT
        elif newest_side == "remote" and cmp.newest_mtime:
            verdict_color = T.ACCENT2
        if cmp.quick:
            verdict_txt += f"  ·  quick {cmp.quick_limit}"   # short: the columns carry the rest
        self._set_verdict_cell(row, 3, verdict_txt, verdict_color)
        self._set_cell(row, 4, cmp.sync_action)

    # ==================================================================
    # Sync
    # ==================================================================

    def _sync_favorites(self) -> None:
        """Tick every favourite row on both sides, then sync them."""
        for lst in (self._local_list, self._remote_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(_R_KIND) == "fav":
                    item.setCheckState(Qt.CheckState.Checked)
        self._sync_selected()

    def _sync_remote_list(self) -> None:
        """Download the other computer's sync list and sync those folders.

        Ticks every remote folder that is on the other computer's sync list,
        plus any local folder with the same name (so pairs sync two-way),
        then runs the sync.
        """
        remote_sync = self._sync_lists.get("remote") or []
        if not remote_sync:
            QMessageBox.information(
                self, "Sync Remote List",
                "The other computer has not published a sync list yet.\n\n"
                "On the other computer, select folders and use the '⇄ Sync List' "
                "button to add them, then connect again.")
            return
        remote_names = {os.path.basename(p.rstrip("/\\")) or p for p in remote_sync}
        for lst in (self._remote_list, self._local_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(_R_NAME) in remote_names:
                    item.setCheckState(Qt.CheckState.Checked)
        self._sync_selected()

    def _update_direction_ui(self) -> None:
        """Mirror-delete is only meaningful for a one-way direction."""
        self._delete_extra_cb.setEnabled(self._direction_combo.currentData() != "auto")

    def _sync_selected(self) -> None:
        if getattr(self, "_sync_checking", False):
            return
        plans = self._selected_plans()
        if not plans:
            QMessageBox.information(self, "Sync", "Tick at least one folder on either side first.")
            return
        if self._peer is None:
            return

        local_root = self._local_root_text()
        remote_root = self._remote_root_text()
        remote_cfg = self._peer.make_endpoint(remote_root)
        host_label = f"{self._peer.username}@{self._peer.host}"
        copy_missing = self._copy_missing_cb.isChecked()

        # Direction chosen in the panel: "auto" keeps the per-folder behaviour
        # (two-way when both sides exist, otherwise towards the side that has
        # the folder), one-way choices force that direction for every pair.
        choice = self._direction_combo.currentData()
        force_mode: Optional[str] = None
        force_direction: Optional[str] = None
        direction_label = ""
        if choice == "local_to_remote":
            force_mode, force_direction, direction_label = "one_way", "local_to_remote", "this PC → other PC"
        elif choice == "remote_to_local":
            force_mode, force_direction, direction_label = "one_way", "remote_to_local", "other PC → this PC"

        to_run: List[PeerPlan] = []
        skipped: List[str] = []
        for plan in plans:
            if plan.both_sides:
                to_run.append(plan)
            elif copy_missing:
                to_run.append(plan)
            else:
                skipped.append(plan.name)

        if not to_run:
            QMessageBox.information(
                self, "Sync", "No folders to sync.\n\nFolders that exist on only one side are skipped because "
                "'Copy folders that exist on only one side' is off.")
            return

        delete_extra = force_mode == "one_way" and self._delete_extra_cb.isChecked()

        # Safety check: scan both sides of every pair first, then show what
        # each folder holds and warn about differences before syncing.
        ctx = {
            "local_root": local_root,
            "remote_root": remote_root,
            "remote_cfg": remote_cfg,
            "host_label": host_label,
            "force_mode": force_mode,
            "force_direction": force_direction,
            "direction_label": direction_label,
            "delete_extra": delete_extra,
            "skipped": skipped,
            "copy_missing": copy_missing,
        }
        self._sync_checking = True
        self._plan_status.setText("Checking folders before sync …")
        self._plan_status.setStyleSheet(f"color: {T.INFO}; font-size: 12px;")
        self._report("Checking folders on both computers before syncing …", "compare", busy=True)
        peer = self._peer

        def _run() -> None:
            results: List[Tuple[PeerPlan, PairCompare]] = []
            try:
                for idx, plan in enumerate(to_run, start=1):
                    self._signals.compare_row.emit(plan.key, "local", f"Checking {idx}/{len(to_run)} …")
                    local_path = plan.local_path or os.path.join(local_root, plan.name)
                    remote_path = plan.remote_path or f"{remote_root.rstrip('/')}/{plan.name}"
                    results.append((plan, compare_pair(peer, local_path, remote_path, plan.name)))
            except Exception as exc:      # noqa: BLE001
                self._sync_checking = False
                self._signals.sync_done.emit(False, f"Pre-sync check failed: {exc}")
                return
            self._signals.safety_checked.emit(results, ctx)

        threading.Thread(target=_run, daemon=True).start()

    def _on_safety_checked(self, results, ctx) -> None:
        """Present the pre-sync safety check; continue only when confirmed."""
        self._sync_checking = False
        any_diff = any(cmp.has_differences for _, cmp in results)
        if any_diff:
            dlg = SyncSafetyDialog(self, results, ctx)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                self._plan_status.setText(
                    "Sync cancelled — the folders look different. Review them, then try again.")
                self._plan_status.setStyleSheet(f"color: {T.WARNING}; font-size: 12px;")
                return
        else:
            lines = []
            direction_label = ctx.get("direction_label") or ""
            for plan, _cmp in results:
                lines.append(f"- {plan.name}  →  {direction_label if direction_label else plan.direction}")
            if ctx.get("delete_extra"):
                lines.append("\nExtra files at the destination will be deleted (mirror).")
            skipped = ctx.get("skipped") or []
            if skipped:
                lines.append(f"\nSkipped (only on one side): {', '.join(skipped)}")
            prompt = "The following folders will be synced:\n\n" + "\n".join(lines)
            prompt += "\n\nEach pair is saved as a profile so it can be re-run or scheduled later. Continue?"
            if QMessageBox.question(
                self, "Confirm Sync", prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        self._start_syncs(results, ctx)

    def _start_syncs(self, results, ctx) -> None:
        started = 0
        for plan, _cmp in results:
            profile = plan.build_profile(
                ctx["local_root"], ctx["remote_root"], ctx["remote_cfg"], ctx["host_label"],
                mode=ctx["force_mode"], direction=ctx["force_direction"],
            )
            if ctx.get("delete_extra"):
                profile.options.delete_extra = True
            profile = find_or_create_peer_profile(self._app.profile_mgr, profile)
            self._app.save_profile(profile)
            self._app.start_sync(profile.id)
            started += 1
        self._plan_status.setStyleSheet("")
        has_monitor = "monitor" in getattr(self._app, "_panels", {})
        self._plan_status.setText(
            f"Started {started} sync job(s)."
            + (" Progress is shown in the Monitor panel." if has_monitor
               else " Copying in the background — close or re-run any time.")
        )
        skipped = ctx.get("skipped") or []
        if skipped:
            self._plan_status.setText(self._plan_status.text() + f"  Skipped: {', '.join(skipped)}")

    def _on_sync_done(self, ok: bool, message: str) -> None:
        self._plan_status.setText(message)
        self._plan_status.setStyleSheet(f"color: {T.SUCCESS if ok else T.ERROR}; font-size: 12px;")
        self._report(message, done=ok)   # close the activity window's running job

    # ==================================================================
    # Plan table helpers
    # ==================================================================

    def _append_plan_row(self, key: str, name: str, local_txt: str, remote_txt: str, verdict: str, action: str) -> int:
        row = self._plan_table.rowCount()
        self._plan_table.insertRow(row)
        name_item = self._set_cell(row, 0, name, bold=True)
        name_item.setData(_R_KEY, key)
        self._set_cell(row, 1, local_txt)
        self._set_cell(row, 2, remote_txt)
        self._set_verdict_cell(row, 3, verdict, T.TEXT_DIM)
        self._set_cell(row, 4, action)
        return row

    def _row_for_key(self, key: str) -> Optional[int]:
        for row in range(self._plan_table.rowCount()):
            item = self._plan_table.item(row, 0)
            if item is not None and item.data(_R_KEY) == key:
                return row
        return None

    def _set_cell(self, row: int, col: int, text: str, bold: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if bold:
            f = QFont()
            f.setBold(True)
            item.setFont(f)
        item.setForeground(QColor(T.TEXT))
        self._plan_table.setItem(row, col, item)
        return item

    def _set_verdict_cell(self, row: int, col: int, text: str, color: str) -> None:
        item = QTableWidgetItem(text)
        f = QFont()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor(color))
        self._plan_table.setItem(row, col, item)
