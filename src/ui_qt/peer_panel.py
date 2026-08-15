
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
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.peer import (
    PairCompare,
    PeerConnection,
    PeerConnectionConfig,
    PeerPlan,
    add_local_favorite,
    add_to_sync_list,
    compare_pair,
    delete_peer_connection,
    find_or_create_peer_profile,
    list_local_folders,
    load_local_favorites,
    load_local_sync_list,
    load_peer_connections,
    read_remote_favorites,
    read_remote_sync_list,
    remove_local_favorite,
    remove_from_sync_list,
    save_local_favorites,
    save_peer_connections,
    touch_peer_connection,
    upsert_peer_connection,
)
from ui_qt import theme as T
from ui_qt.widgets import (
    GhostButton,
    GlassCard,
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


class PeerSyncPanel(QWidget):
    """Side-by-side folder selection and sync between two computers."""

    def __init__(self, app: "QueekSyncApp", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
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

        scroll = ScrollArea(self)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(T.PAD_LG, T.PAD_MD, T.PAD_LG, T.PAD_LG)
        host_layout.setSpacing(T.PAD_MD)
        scroll.setWidget(host)
        root.addWidget(scroll)

        self._status_lbl = MutedLabel("Connect to the other computer to begin.")
        host_layout.addWidget(self._status_lbl)

        # ── Saved connections panel ────────────────────────────────────
        saved_card = GlassCard(host)
        saved_layout = QVBoxLayout(saved_card)
        saved_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        saved_layout.setSpacing(T.PAD_SM)
        saved_layout.addWidget(SectionLabel("Saved connections"))

        self._conn_list = QListWidget()
        self._conn_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._conn_list.setMinimumHeight(96)
        self._conn_list.setMaximumHeight(180)
        self._conn_list.currentRowChanged.connect(self._on_conn_selected)
        self._conn_list.itemDoubleClicked.connect(self._load_connection)
        self._conn_list.setStyleSheet(
            f"QListWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {T.BG_HOVER};"
            f" border: 1px solid {T.ACCENT}; }}"
        )
        saved_layout.addWidget(self._conn_list)

        saved_btn_row = QHBoxLayout()
        self._load_conn_btn = PrimaryButton("Load", saved_card, command=self._load_connection)
        saved_btn_row.addWidget(self._load_conn_btn)
        save_btn = GhostButton("Save current connection", saved_card, command=self._save_connection)
        saved_btn_row.addWidget(save_btn)
        self._delete_conn_btn = GhostButton("Delete", saved_card, command=self._delete_connection)
        saved_btn_row.addWidget(self._delete_conn_btn)
        saved_btn_row.addStretch()
        saved_layout.addLayout(saved_btn_row)

        self._conn_hint = MutedLabel("")
        saved_layout.addWidget(self._conn_hint)
        host_layout.addWidget(saved_card)
        attach_tooltip(self._conn_list, "Select a saved SSH connection and click Load to connect, "
                                        "or click Save current connection to store the details you typed.")

        # ── 1 · Connection ────────────────────────────────────────────
        conn_card = GlassCard(host)
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        conn_layout.setSpacing(T.PAD_SM)
        conn_layout.addWidget(SectionLabel("1 · Connect to the other computer"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(T.PAD_SM)
        grid.setVerticalSpacing(T.PAD_SM)
        self._host_entry = LabelledEntry("IP address or hostname", placeholder="192.168.1.50")
        self._port_entry = LabelledEntry("SSH port", placeholder="22")
        self._port_entry.set("22")
        self._user_entry = LabelledEntry("Username")
        self._pass_entry = LabelledEntry("Password", show="●")
        grid.addWidget(self._host_entry, 0, 0)
        grid.addWidget(self._port_entry, 0, 1)
        grid.addWidget(self._user_entry, 1, 0)
        grid.addWidget(self._pass_entry, 1, 1)
        conn_layout.addLayout(grid)

        conn_row = QHBoxLayout()
        self._remember_cb = QCheckBox("Remember password")
        self._remember_cb.setChecked(True)
        conn_row.addWidget(self._remember_cb)
        attach_tooltip(self._remember_cb, "Store the password with the saved connection. Stored in plaintext in the "
                                         "app config folder, like SFTP profile passwords.")
        self._connect_btn = PrimaryButton("Connect", conn_card, command=self._connect)
        conn_row.addWidget(self._connect_btn)
        self._disconnect_btn = GhostButton("Disconnect", conn_card, command=self._disconnect)
        self._disconnect_btn.setEnabled(False)
        conn_row.addWidget(self._disconnect_btn)
        conn_row.addStretch()
        self._conn_detail = MutedLabel("")
        conn_row.addWidget(self._conn_detail)
        conn_layout.addLayout(conn_row)
        host_layout.addWidget(conn_card)

        # ── 2 · Folder roots & lists ──────────────────────────────────
        root_card = GlassCard(host)
        root_layout = QVBoxLayout(root_card)
        root_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        root_layout.setSpacing(T.PAD_SM)
        root_layout.addWidget(SectionLabel("2 · Choose folders to sync"))

        roots_row = QHBoxLayout()
        self._local_root = LabelledEntry("This computer – base folder", placeholder=str(os.path.expanduser("~")))
        self._local_root.set(os.path.expanduser("~"))
        roots_row.addWidget(self._local_root, 1)
        browse_btn = GhostButton("Browse…", root_card, command=self._browse_local_root)
        browse_btn.setFixedSize(90, 34)
        roots_row.addWidget(browse_btn, 0, Qt.AlignmentFlag.AlignBottom)
        fav_btn = GhostButton("★  Favourite", root_card, command=self._toggle_favorite)
        fav_btn.setFixedSize(100, 34)
        roots_row.addWidget(fav_btn, 0, Qt.AlignmentFlag.AlignBottom)
        sync_btn = GhostButton("⇄  Sync List", root_card, command=self._toggle_sync_list)
        sync_btn.setFixedSize(100, 34)
        roots_row.addWidget(sync_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self._remote_root = LabelledEntry("Other computer – base folder", placeholder="/home/user")
        roots_row.addWidget(self._remote_root, 1)
        refresh_btn = GhostButton("⟳ Refresh", root_card, command=self._reload_folders)
        refresh_btn.setFixedSize(100, 34)
        roots_row.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignBottom)
        root_layout.addLayout(roots_row)
        attach_tooltip(fav_btn, "Add (or remove) the base folder shown above as a favourite. "
                               "The other computer sees it when it connects.")
        attach_tooltip(sync_btn, "Add (or remove) the base folder shown above to your sync list. "
                                "The other computer can then sync it with one click.")

        lists_row = QHBoxLayout()
        lists_row.setSpacing(T.PAD_MD)
        self._local_list, self._local_header = self._make_folder_list(
            "Folders on this computer  (★ favourite · ⇄ sync list)", lists_row)
        self._remote_list, self._remote_header = self._make_folder_list(
            "Folders on the other computer  (★ favourite · ⇄ sync list)", lists_row)
        root_layout.addLayout(lists_row, 1)
        host_layout.addWidget(root_card)

        # ── 3 · Plan / compare / sync ─────────────────────────────────
        plan_card = GlassCard(host)
        plan_layout = QVBoxLayout(plan_card)
        plan_layout.setContentsMargins(T.PAD_MD, T.PAD_MD, T.PAD_MD, T.PAD_MD)
        plan_layout.setSpacing(T.PAD_SM)
        plan_layout.addWidget(SectionLabel("3 · Review and sync"))

        self._plan_table = QTableWidget(0, 5)
        self._plan_table.setHorizontalHeaderLabels(
            ["Folder", "This PC", "Other PC", "Newer side", "Action"]
        )
        self._plan_table.verticalHeader().setVisible(False)
        # Rows must grow to fit multi-line cells (e.g. "…\nLast updated: …");
        # the default Interactive mode keeps every row at 30px and clips the
        # second line, hiding the dates/times.
        _vh = self._plan_table.verticalHeader()
        assert _vh is not None
        _vh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._plan_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._plan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self._plan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._plan_table.setMinimumHeight(160)
        self._plan_table.setMaximumHeight(260)
        self._plan_table.setStyleSheet(
            f"QTableWidget {{ background-color: {T.BG_INPUT}; border: 1px solid {T.BORDER};"
            f" border-radius: 8px; gridline-color: {T.BORDER}; }}"
            f"QHeaderView::section {{ background-color: {T.BG_CARD}; color: {T.TEXT_MUTED};"
            f" border: none; padding: 6px; font-weight: 600; }}"
            f"QTableWidget::item {{ padding: 4px 8px; }}"
            f"QTableWidget::item:selected {{ background-color: {T.BG_HOVER}; }}"
        )
        plan_layout.addWidget(self._plan_table)

        direction_row = QHBoxLayout()
        direction_row.addWidget(MutedLabel("Sync direction"))
        self._direction_combo = QComboBox()
        self._direction_combo.addItem("Two-way (automatic)", "auto")
        self._direction_combo.addItem("This PC → Other PC", "local_to_remote")
        self._direction_combo.addItem("Other PC → This PC", "remote_to_local")
        self._direction_combo.setMinimumWidth(220)
        direction_row.addWidget(self._direction_combo)
        self._delete_extra_cb = QCheckBox("Delete extra files at destination")
        self._delete_extra_cb.setEnabled(False)
        direction_row.addWidget(self._delete_extra_cb)
        direction_row.addStretch()
        plan_layout.addLayout(direction_row)
        attach_tooltip(self._direction_combo,
                       "Two-way (automatic): folders on both computers sync both ways, folders on one side only are "
                       "copied across. Pick a one-way direction to always copy that way instead.")
        attach_tooltip(self._delete_extra_cb,
                       "When a one-way direction is chosen, also delete files at the destination that no longer "
                       "exist in the source (mirror sync). Not available for two-way.")
        self._direction_combo.currentIndexChanged.connect(self._update_direction_ui)

        plan_row = QHBoxLayout()
        self._copy_missing_cb = QCheckBox("Copy folders that exist on only one side")
        self._copy_missing_cb.setChecked(True)
        plan_row.addWidget(self._copy_missing_cb)
        plan_row.addStretch()
        compare_btn = GhostButton("≋  Compare Selected", plan_card, command=self._compare_selected)
        plan_row.addWidget(compare_btn)
        self._fav_sync_btn = GhostButton("★  Sync Favourites", plan_card, command=self._sync_favorites)
        plan_row.addWidget(self._fav_sync_btn)
        self._sync_remote_btn = GhostButton("⇄  Sync Remote List", plan_card, command=self._sync_remote_list)
        plan_row.addWidget(self._sync_remote_btn)
        self._sync_btn = PrimaryButton("▶  Sync Selected", plan_card, command=self._sync_selected)
        plan_row.addWidget(self._sync_btn)
        plan_layout.addLayout(plan_row)

        self._plan_status = MutedLabel("Tick folders on either side, then Compare or Sync.")
        plan_layout.addWidget(self._plan_status)
        host_layout.addWidget(plan_card)
        host_layout.addStretch()

        attach_tooltip(compare_btn, "Scan the selected folders on both computers, show which side is newer, and report the date/time each folder's content was last updated.")
        attach_tooltip(self._sync_btn, "Sync the selected folder pairs (two-way when both sides exist).")
        attach_tooltip(self._fav_sync_btn, "Select every favourite folder on both computers and sync them.")
        attach_tooltip(self._sync_remote_btn, "Download the other computer's sync list and sync those folders.")
        attach_tooltip(self._copy_missing_cb, "Folders checked on one side only are copied to the other side.")

        self._set_connected_ui(False)
        self._populate_connections()

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
        lst.setMinimumHeight(220)
        lst.setMaximumHeight(320)
        lst.itemChanged.connect(self._on_item_changed)
        lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lst.customContextMenuRequested.connect(self._on_list_context_menu)
        attach_tooltip(lst, "Right-click a ★ favourite folder to remove it from favourites.")
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

    def _populate_connections(self) -> None:
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
        self._delete_conn_btn.setEnabled(has_connections)
        if has_connections:
            self._conn_list.setCurrentRow(0)
            self._fill_connection_fields(self._connections[0])
            self._conn_hint.setText("Select a connection and click Load to connect to it.")
        else:
            self._conn_hint.setText("No saved connections yet - enter the details below, "
                                    "then click 'Save current connection'.")

    @staticmethod
    def _conn_label(c: PeerConnectionConfig) -> str:
        return f"{c.name}  ({c.username}@{c.host}:{c.port})"

    def _fill_connection_fields(self, c: PeerConnectionConfig) -> None:
        self._host_entry.set(c.host)
        self._port_entry.set(str(c.port))
        self._user_entry.set(c.username)
        self._pass_entry.set(c.password if c.remember_password else "")
        self._remember_cb.setChecked(c.remember_password)

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
        row = self._conn_list.currentRow()
        if 0 <= row < len(self._connections):
            return self._connections[row].name
        user = self._user_entry.get().strip()
        host = self._host_entry.get().strip()
        text = f"{user}@{host}" if user else host
        return text

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
        name = self._current_connection_name()
        conn = PeerConnectionConfig(
            name=name or host,
            host=host,
            port=port,
            username=user,
            password=self._pass_entry.get(),
            remember_password=self._remember_cb.isChecked(),
        )
        is_new = upsert_peer_connection(conn)
        self._populate_connections()
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
            return

        self._set_connected_ui(True)
        label = f"{self._peer.username}@{self._peer.host}:{self._peer.port}"
        self._remote_header.setText(f"Folders on the other computer ({label})  (★ favourite · ⇄ sync list)")
        self._remote_root.set(message)
        self._conn_detail.setText(f"Connected · home: {message}")
        self._status_lbl.setText(f"Connected to {label}.")
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")

        # move the matching saved connection to the top (most recently used)
        match = next(
            (c for c in self._connections
             if c.host == self._peer.host and c.port == self._peer.port and c.username == self._peer.username),
            None,
        )
        if match is not None and self._connections and self._connections[0].name != match.name:
            touch_peer_connection(match.name)
            self._populate_connections()
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
                remote_favs = read_remote_favorites(self._peer)
                remote_sync = read_remote_sync_list(self._peer)
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

    def _on_list_context_menu(self, pos) -> None:
        """Right-click menu on a folder row: remove a favourite folder."""
        lst = self.sender()
        if not isinstance(lst, QListWidget) or (lst is not self._local_list and lst is not self._remote_list):
            return
        item = lst.itemAt(pos)
        if item is None:
            return
        kind = item.data(_R_KIND)
        if kind != "fav":
            return
        path = item.data(_R_PATH)
        name = item.data(_R_NAME)

        menu = QMenu(self)
        if lst is self._local_list:
            # Destructive action → follow the app's DangerButton look
            # (red on dark red) instead of the default washed-out menu text.
            menu.setStyleSheet(
                f"QMenu {{ background-color: {T.BG_CARD}; border: 1px solid {T.BORDER};"
                f" border-radius: {T.RADIUS_MD}px; padding: 6px; }}"
                f"QMenu::item {{ background: transparent; color: {T.ERROR};"
                f" padding: 7px 14px; border-radius: {T.RADIUS_SM}px; }}"
                f"QMenu::item:selected {{ background-color: #7f1d1d; }}"
            )
            act = menu.addAction("★  Remove from favourites")
            assert act is not None
            act.triggered.connect(lambda: self._remove_favorite(path, name))
        else:
            menu.setStyleSheet(
                f"QMenu {{ background-color: {T.BG_CARD}; border: 1px solid {T.BORDER};"
                f" border-radius: {T.RADIUS_MD}px; padding: 6px; }}"
                f"QMenu::item {{ background: transparent; color: {T.TEXT_DIM};"
                f" padding: 7px 14px; border-radius: {T.RADIUS_SM}px; }}"
            )
            act = menu.addAction("Managed on the other computer")
            assert act is not None
            act.setEnabled(False)
            act.setToolTip("This is the other computer's favourites list. Remove folders there "
                           "on that computer's Peer Sync page.")
        menu.exec(lst.viewport().mapToGlobal(pos))

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
        if self._peer is None:
            return
        plans = self._selected_plans()
        if not plans:
            QMessageBox.information(self, "Compare", "Tick at least one folder on either side first.")
            return
        self._plan_table.setRowCount(0)
        for plan in plans:
            self._append_plan_row(plan.key, plan.name, "scanning", "scanning", "…", "…")
        self._plan_status.setText(f"Comparing {len(plans)} folder(s) …")

        local_root = self._local_root_text()
        remote_root = self._remote_root_text()

        def _run() -> None:
            for idx, plan in enumerate(plans, start=1):
                self._signals.compare_row.emit(plan.key, "local", f"Scanning {idx}/{len(plans)} …")
                local_path = plan.local_path or os.path.join(local_root, plan.name)
                remote_path = plan.remote_path or f"{remote_root.rstrip('/')}/{plan.name}"
                cmp = compare_pair(self._peer, local_path, remote_path, plan.name)
                self._signals.pair_compared.emit(plan.key, cmp)
            self._signals.sync_done.emit(True, f"Compare finished for {len(plans)} folder(s).")

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
        local_txt = cmp.local.summary_line() if (cmp.local.error or cmp.local.missing) else (
            f"{cmp.local.file_count} file(s) · {_human_size(cmp.local.total_size)}\n"
            f"Last updated: {_fmt_mtime(cmp.local.latest_mtime)}"
        )
        remote_txt = cmp.remote.summary_line() if (cmp.remote.error or cmp.remote.missing) else (
            f"{cmp.remote.file_count} file(s) · {_human_size(cmp.remote.total_size)}\n"
            f"Last updated: {_fmt_mtime(cmp.remote.latest_mtime)}"
        )
        self._set_cell(row, 1, local_txt)
        self._set_cell(row, 2, remote_txt)
        verdict_txt = _VERDICT_LABELS.get(cmp.verdict, cmp.verdict)
        verdict_color = _VERDICT_COLORS.get(cmp.verdict, T.TEXT)
        # Always report the date/time the folder content was last updated,
        # and which side holds it when that is meaningful.
        when = _fmt_mtime(cmp.newest_mtime)
        newest_side = cmp.newest_side
        if newest_side == "local" and cmp.newest_mtime:
            verdict_txt += f"\nLast updated: this PC · {when}"
            verdict_color = T.ACCENT
        elif newest_side == "remote" and cmp.newest_mtime:
            verdict_txt += f"\nLast updated: other PC · {when}"
            verdict_color = T.ACCENT2
        elif newest_side == "tie" and cmp.newest_mtime:
            # Both sides updated within 2s of each other — one shared time.
            verdict_txt += f"\nLast updated: {when}"
        elif cmp.verdict == "only_local" and cmp.local.latest_mtime:
            verdict_txt += f"\nLast updated: {_fmt_mtime(cmp.local.latest_mtime)}"
        elif cmp.verdict == "only_remote" and cmp.remote.latest_mtime:
            verdict_txt += f"\nLast updated: {_fmt_mtime(cmp.remote.latest_mtime)}"
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
        peer = self._peer

        def _run() -> None:
            results: List[Tuple[PeerPlan, PairCompare]] = []
            for idx, plan in enumerate(to_run, start=1):
                self._signals.compare_row.emit(plan.key, "local", f"Checking {idx}/{len(to_run)} …")
                local_path = plan.local_path or os.path.join(local_root, plan.name)
                remote_path = plan.remote_path or f"{remote_root.rstrip('/')}/{plan.name}"
                results.append((plan, compare_pair(peer, local_path, remote_path, plan.name)))
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
        self._plan_status.setText(f"Started {started} sync job(s). Progress is shown in the Monitor panel.")
        skipped = ctx.get("skipped") or []
        if skipped:
            self._plan_status.setText(self._plan_status.text() + f"  Skipped: {', '.join(skipped)}")

    def _on_sync_done(self, ok: bool, message: str) -> None:
        self._plan_status.setText(message)
        self._plan_status.setStyleSheet(f"color: {T.SUCCESS if ok else T.ERROR}; font-size: 12px;")

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
