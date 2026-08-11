
"""
Peer Sync panel – sync folders between two computers over SSH (PyQt6).

Flow:
  1. Connect to the other computer (IP, username, password).
  2. Browse folders on both machines side by side and tick the ones to sync.
  3. Compare selected pairs to see which side is newer.
  4. Sync the selected pairs through the existing sync engine.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.peer import (
    PairCompare,
    PeerConnection,
    PeerPlan,
    compare_pair,
    find_or_create_peer_profile,
    list_local_folders,
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
    "in_sync":     "In sync",
    "local_newer": "This PC newer",
    "remote_newer": "Other PC newer",
    "mixed":       "Both changed",
    "only_local":  "Only on this PC",
    "only_remote": "Only on other PC",
    "empty":       "Empty folder",
    "error":       "Error",
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


class _PeerSignals(QObject):
    """Thread-safe signal bridge for background workers."""

    connected = pyqtSignal(bool, str)          # ok, message
    folders_loaded = pyqtSignal(str, list)     # side ("local"/"remote"), entries
    compare_row = pyqtSignal(str, str, str)    # name, side, status
    pair_compared = pyqtSignal(str, object)    # name, PairCompare
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


class PeerSyncPanel(QWidget):
    """Side-by-side folder selection and sync between two computers."""

    def __init__(self, app: "QueekSyncApp", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app
        self._peer: Optional[PeerConnection] = None
        self._signals = _PeerSignals()
        self._plan: Dict[str, PeerPlan] = {}

        self._signals.connected.connect(self._on_connected)
        self._signals.folders_loaded.connect(self._on_folders_loaded)
        self._signals.compare_row.connect(self._on_compare_row)
        self._signals.pair_compared.connect(self._on_pair_compared)
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
        self._remote_root = LabelledEntry("Other computer – base folder", placeholder="/home/user")
        roots_row.addWidget(self._remote_root, 1)
        refresh_btn = GhostButton("⟳ Refresh", root_card, command=self._reload_folders)
        refresh_btn.setFixedSize(100, 34)
        roots_row.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignBottom)
        root_layout.addLayout(roots_row)

        lists_row = QHBoxLayout()
        lists_row.setSpacing(T.PAD_MD)
        self._local_list, self._local_header = self._make_folder_list(
            "Folders on this computer", lists_row)
        self._remote_list, self._remote_header = self._make_folder_list(
            "Folders on the other computer", lists_row)
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

        plan_row = QHBoxLayout()
        self._copy_missing_cb = QCheckBox("Copy folders that exist on only one side")
        self._copy_missing_cb.setChecked(True)
        plan_row.addWidget(self._copy_missing_cb)
        plan_row.addStretch()
        compare_btn = GhostButton("≋  Compare Selected", plan_card, command=self._compare_selected)
        plan_row.addWidget(compare_btn)
        self._sync_btn = PrimaryButton("▶  Sync Selected", plan_card, command=self._sync_selected)
        plan_row.addWidget(self._sync_btn)
        plan_layout.addLayout(plan_row)

        self._plan_status = MutedLabel("Tick folders on either side, then Compare or Sync.")
        plan_layout.addWidget(self._plan_status)
        host_layout.addWidget(plan_card)
        host_layout.addStretch()

        attach_tooltip(compare_btn, "Scan the selected folders on both computers and show which side is newer.")
        attach_tooltip(self._sync_btn, "Sync the selected folder pairs (two-way when both sides exist).")
        attach_tooltip(self._copy_missing_cb, "Folders checked on one side only are copied to the other side.")

        self._set_connected_ui(False)

    def _make_folder_list(self, title: str, parent_layout: QHBoxLayout) -> tuple:
        col = QWidget()
        col_layout = QVBoxLayout(col)
        col_layout.setContentsMargins(0, 0, 0, 0)
        col_layout.setSpacing(4)
        header = MutedLabel(title)
        header.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px; font-weight: 600;")
        col_layout.addWidget(header)

        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lst.setMinimumHeight(220)
        lst.setMaximumHeight(320)
        lst.itemChanged.connect(self._on_item_changed)
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

    def _on_connected(self, ok: bool, message: str) -> None:
        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("Connect")
        if not ok:
            self._status_lbl.setText(f"Connection failed: {message[:120]}")
            self._status_lbl.setStyleSheet(f"color: {T.ERROR}; font-size: 12px;")
            return

        self._set_connected_ui(True)
        label = f"{self._peer.username}@{self._peer.host}:{self._peer.port}"
        self._remote_header.setText(f"Folders on the other computer ({label})")
        self._remote_root.set(message)
        self._conn_detail.setText(f"Connected · home: {message}")
        self._status_lbl.setText(f"Connected to {label}.")
        self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
        self._reload_folders()

    def _disconnect(self) -> None:
        if self._peer is not None:
            try:
                self._peer.disconnect()
            except Exception:
                pass
            self._peer = None
        self._set_connected_ui(False)
        self._local_list.clear()
        self._remote_list.clear()
        self._plan_table.setRowCount(0)
        self._plan.clear()
        self._remote_header.setText("Folders on the other computer")
        self._conn_detail.setText("")
        self._status_lbl.setText("Disconnected. Enter the other computer's details to reconnect.")
        self._status_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")

    def _set_connected_ui(self, connected: bool) -> None:
        self._disconnect_btn.setEnabled(connected)
        self._sync_btn.setEnabled(connected)

    # ==================================================================
    # Folder listing
    # ==================================================================

    def _browse_local_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select base folder on this computer")
        if path:
            self._local_root.set(path)
            self._reload_folders()

    def _reload_folders(self) -> None:
        if self._peer is None:
            return
        local_path = os.path.expanduser(self._local_root.get().strip()) or os.path.expanduser("~")
        remote_path = self._remote_root.get().strip() or self._peer.home_dir
        self._local_root.set(local_path)
        self._remote_root.set(remote_path)
        self._plan.clear()
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

            remote_err = ""
            remote_folders: List[dict] = []
            if self._peer is not None:
                try:
                    remote_folders = self._peer.list_folders(remote_path)
                except Exception as exc:
                    remote_err = str(exc)
            self._signals.folders_loaded.emit("remote", remote_folders if not remote_err else [])

            if local_err or remote_err:
                self._signals.connected.emit(False, "")
                self._signals.sync_done.emit(False, (f"Could not list folders:\n"
                                                     f"  local: {local_err}\n  remote: {remote_err}"))

        threading.Thread(target=_load, daemon=True).start()

    def _on_folders_loaded(self, side: str, folders: List[dict]) -> None:
        lst = self._local_list if side == "local" else self._remote_list
        lst.blockSignals(True)
        lst.clear()
        for f in folders:
            item = QListWidgetItem(f"{f['name']}   ·   {_fmt_mtime(f['mtime'])}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, f["name"])
            item.setData(Qt.ItemDataRole.UserRole + 1, f["mtime"])
            lst.addItem(item)
        lst.blockSignals(False)

        other_side = "remote" if side == "local" else "local"
        if not self._folders_loaded(other_side):
            self._status_lbl.setText(f"Loaded {len(folders)} folder(s) on {'this' if side == 'local' else 'other'} computer.")
            self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
        else:
            self._status_lbl.setText(f"Loaded folders on both computers ({len(folders)} on this side).")
            self._status_lbl.setStyleSheet(f"color: {T.SUCCESS}; font-size: 12px;")
        self._plan_status.setText("Tick folders on either side, then Compare or Sync.")

    def _folders_loaded(self, side: str) -> bool:
        lst = self._local_list if side == "local" else self._remote_list
        return lst.count() > 0

    # ==================================================================
    # Selection
    # ==================================================================

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        plan = self._plan.setdefault(name, PeerPlan(name=name))
        if item.listWidget() is self._local_list:
            plan.local_checked = item.checkState() == Qt.CheckState.Checked
        else:
            plan.remote_checked = item.checkState() == Qt.CheckState.Checked
        self._plan_status.setText(
            f"{len(self._selected_names())} folder(s) selected. "
            "Use Compare to preview or Sync to run."
        )

    def _selected_names(self) -> List[str]:
        names: set = set()
        for lst in (self._local_list, self._remote_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    names.add(item.data(Qt.ItemDataRole.UserRole))
        return sorted(names)

    def _plan_from_selection(self) -> List[PeerPlan]:
        plans: List[PeerPlan] = []
        for name in self._selected_names():
            plan = self._plan.setdefault(name, PeerPlan(name=name))
            plans.append(plan)
        return plans

    # ==================================================================
    # Compare
    # ==================================================================

    def _compare_selected(self) -> None:
        if self._peer is None:
            return
        names = self._selected_names()
        if not names:
            QMessageBox.information(self, "Compare", "Tick at least one folder on either side first.")
            return
        self._plan_table.setRowCount(0)
        for name in names:
            self._append_plan_row(name, "scanning", "scanning", "…", "…")
        self._plan_status.setText(f"Comparing {len(names)} folder(s) …")

        local_root = os.path.expanduser(self._local_root.get().strip())
        remote_root = self._remote_root.get().strip()

        def _run() -> None:
            for idx, name in enumerate(names, start=1):
                self._signals.compare_row.emit(name, "local", f"Scanning {idx}/{len(names)} …")
                cmp = compare_pair(
                    self._peer,
                    os.path.join(local_root, name),
                    f"{remote_root.rstrip('/')}/{name}",
                    name,
                )
                self._signals.pair_compared.emit(name, cmp)
            self._signals.sync_done.emit(True, f"Compare finished for {len(names)} folder(s).")

        threading.Thread(target=_run, daemon=True).start()

    def _on_compare_row(self, name: str, side: str, status: str) -> None:
        row = self._row_for_name(name)
        if row is None:
            return
        col = 1 if side == "local" else 2
        item = self._plan_table.item(row, col)
        if item is not None:
            item.setText(status)

    def _on_pair_compared(self, name: str, cmp: PairCompare) -> None:
        plan = self._plan.get(name)
        if plan is not None:
            plan.compare = cmp
        row = self._row_for_name(name)
        if row is None:
            return
        local_txt = cmp.local.summary_line() if (cmp.local.error or cmp.local.missing) else (
            f"{cmp.local.file_count} file(s) · {_human_size(cmp.local.total_size)}\nlatest {_fmt_mtime(cmp.local.latest_mtime)}"
        )
        remote_txt = cmp.remote.summary_line() if (cmp.remote.error or cmp.remote.missing) else (
            f"{cmp.remote.file_count} file(s) · {_human_size(cmp.remote.total_size)}\nlatest {_fmt_mtime(cmp.remote.latest_mtime)}"
        )
        self._set_cell(row, 1, local_txt)
        self._set_cell(row, 2, remote_txt)
        self._set_verdict_cell(row, 3, _VERDICT_LABELS.get(cmp.verdict, cmp.verdict),
                               _VERDICT_COLORS.get(cmp.verdict, T.TEXT))
        self._set_cell(row, 4, cmp.sync_action)

    # ==================================================================
    # Sync
    # ==================================================================

    def _sync_selected(self) -> None:
        plans = self._plan_from_selection()
        if not plans:
            QMessageBox.information(self, "Sync", "Tick at least one folder on either side first.")
            return
        if self._peer is None:
            return

        local_root = os.path.expanduser(self._local_root.get().strip())
        remote_root = self._remote_root.get().strip()
        remote_cfg = self._peer.make_endpoint(remote_root)
        host_label = f"{self._peer.username}@{self._peer.host}"
        copy_missing = self._copy_missing_cb.isChecked()

        to_run: List[tuple] = []  # (profile, plan)
        skipped: List[str] = []
        for plan in plans:
            if plan.both_sides:
                to_run.append((plan.build_profile(local_root, remote_root, remote_cfg, host_label), plan))
            elif copy_missing:
                to_run.append((plan.build_profile(local_root, remote_root, remote_cfg, host_label), plan))
            else:
                skipped.append(plan.name)

        if not to_run:
            QMessageBox.information(
                self, "Sync", "No folders to sync.\n\nFolders that exist on only one side are skipped because "
                "'Copy folders that exist on only one side' is off.")
            return

        lines = []
        for profile, plan in to_run:
            lines.append(f"- {plan.name}  →  {plan.direction}")
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

        started = 0
        for profile, plan in to_run:
            profile = find_or_create_peer_profile(self._app.profile_mgr, profile)
            self._app.save_profile(profile)
            self._app.start_sync(profile.id)
            started += 1
        self._plan_status.setText(f"Started {started} sync job(s). Progress is shown in the Monitor panel.")
        if skipped:
            self._plan_status.setText(self._plan_status.text() + f"  Skipped: {', '.join(skipped)}")

    def _on_sync_done(self, ok: bool, message: str) -> None:
        self._plan_status.setText(message)
        self._plan_status.setStyleSheet(f"color: {T.SUCCESS if ok else T.ERROR}; font-size: 12px;")

    # ==================================================================
    # Plan table helpers
    # ==================================================================

    def _append_plan_row(self, name: str, local_txt: str, remote_txt: str, verdict: str, action: str) -> int:
        row = self._plan_table.rowCount()
        self._plan_table.insertRow(row)
        self._set_cell(row, 0, name, bold=True)
        self._set_cell(row, 1, local_txt)
        self._set_cell(row, 2, remote_txt)
        self._set_verdict_cell(row, 3, verdict, T.TEXT_DIM)
        self._set_cell(row, 4, action)
        return row

    def _row_for_name(self, name: str) -> Optional[int]:
        for row in range(self._plan_table.rowCount()):
            item = self._plan_table.item(row, 0)
            if item is not None and item.text() == name:
                return row
        return None

    def _set_cell(self, row: int, col: int, text: str, bold: bool = False) -> None:
        item = QTableWidgetItem(text)
        if bold:
            f = QFont()
            f.setBold(True)
            item.setFont(f)
        item.setForeground(QColor(T.TEXT))
        self._plan_table.setItem(row, col, item)

    def _set_verdict_cell(self, row: int, col: int, text: str, color: str) -> None:
        item = QTableWidgetItem(text)
        f = QFont()
        f.setBold(True)
        item.setFont(f)
        item.setForeground(QColor(color))
        self._plan_table.setItem(row, col, item)
