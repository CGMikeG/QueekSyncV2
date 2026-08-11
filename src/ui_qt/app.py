"""
Main application window and top-level wiring (PyQt6).
"""

from __future__ import annotations

import getpass
import os
import queue
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import ConfigManager
from core.profile import ProfileManager, get_delete_permission_issue
from core.scheduler import SyncScheduler
from core.syncer import SyncEngine, SyncEvent
from core.watcher import WatcherManager
from ui_qt import theme as T
from ui_qt.sidebar import Sidebar

_PAGE_TITLES = {
    "dashboard": "Dashboard",
    "peer":      "Peer Sync",
    "profiles":  "Profiles",
    "monitor":   "Monitor",
    "settings":  "Settings",
}


class QueekSyncApp(QMainWindow):
    """Application entry-point; owns the main QMainWindow and all shared state."""

    def __init__(self) -> None:
        super().__init__()

        # ---- configuration & data ---------------------------------
        self.config_mgr = ConfigManager()
        self.profile_mgr = ProfileManager()
        cfg = self.config_mgr.config

        # ---- window ------------------------------------------------
        self.setWindowTitle("QueekSync")
        self.resize(cfg.window_width, cfg.window_height)
        self.setMinimumSize(900, 600)
        self._apply_stylesheet()

        # ---- shared runtime state ---------------------------------
        self._engines: Dict[str, SyncEngine] = {}
        self._event_queue: "queue.Queue[SyncEvent]" = queue.Queue()
        self._sync_requests: "queue.Queue[str]" = queue.Queue()  # profile_id
        self._log_file_path: str = ""
        self._log_fh = None
        self.refresh_file_logging()

        # ---- background services ----------------------------------
        self._scheduler = SyncScheduler(on_trigger=self._queue_sync_request)
        self._watcher_mgr = WatcherManager(on_change=self._queue_sync_request)

        for p in self.profile_mgr.all():
            self._scheduler.update_profile(p)
            self._watcher_mgr.update(p)
        self._scheduler.start()

        # ---- build UI ---------------------------------------------
        self._panels: Dict[str, QWidget] = {}
        self._active_panel: str = ""
        self._build_ui()

        # ---- start event pump -------------------------------------
        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump_events)
        self._pump_timer.start(100)

    # ==================================================================
    # UI construction
    # ==================================================================

    def _apply_stylesheet(self) -> None:
        theme = self.config_mgr.config.theme
        self.setStyleSheet(T.build_qss(light=theme == "light"))

    def set_theme(self, mode: str) -> None:
        """Apply a theme immediately (dark / light / system)."""
        self.config_mgr.config.theme = mode
        self.config_mgr.save()
        self._apply_stylesheet()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar(on_navigate=self.navigate)
        root.addWidget(self.sidebar)

        # Content container
        content = QWidget(central)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Header bar
        header = QWidget(content)
        header.setFixedHeight(T.HEADER_H)
        header.setStyleSheet(f"background-color: {T.BG_PANEL};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(T.PAD_LG, 0, T.PAD_LG, 0)

        self._header_title = QLabel("Dashboard")
        self._header_title.setObjectName("PageTitle")
        header_layout.addWidget(self._header_title)
        header_layout.addStretch()
        content_layout.addWidget(header)

        # Panel host (stacked)
        self._stack = QStackedWidget(content)
        content_layout.addWidget(self._stack, 1)

        root.addWidget(content, 1)

        self.navigate("dashboard")

    # ==================================================================
    # Navigation
    # ==================================================================

    def navigate(self, page_id: str) -> None:
        if page_id not in self._panels:
            panel = self._create_panel(page_id)
            self._panels[page_id] = panel
            self._stack.addWidget(panel)

        self._stack.setCurrentWidget(self._panels[page_id])
        self._active_panel = page_id
        self.sidebar.set_active(page_id)
        self._header_title.setText(_PAGE_TITLES.get(page_id, page_id.title()))

    def _create_panel(self, page_id: str) -> QWidget:
        from ui_qt.dashboard import DashboardPanel
        from ui_qt.monitor_panel import MonitorPanel
        from ui_qt.profiles_panel import ProfilesPanel
        from ui_qt.settings_panel import SettingsPanel

        if page_id == "dashboard":
            return DashboardPanel(self)
        if page_id == "peer":
            from ui_qt.peer_panel import PeerSyncPanel
            return PeerSyncPanel(self)
        if page_id == "profiles":
            return ProfilesPanel(self)
        if page_id == "monitor":
            return MonitorPanel(self)
        if page_id == "settings":
            return SettingsPanel(self)
        return QWidget(self)

    def refresh_panel(self, page_id: str) -> None:
        """Rebuild a panel so it picks up data changes (keeps it in the stack)."""
        panel = self._panels.get(page_id)
        if panel is not None:
            panel.deleteLater()
            del self._panels[page_id]
            self._stack.removeWidget(panel)
        if self._active_panel == page_id:
            self.navigate(page_id)

    def save_profile(self, profile) -> None:
        """Shared on_save handler for the profile editor."""
        self.profile_mgr.save(profile)
        self._scheduler.update_profile(profile)
        self._watcher_mgr.update(profile)
        self.refresh_panel("dashboard")
        self.refresh_panel("profiles")

    # ==================================================================
    # Sync operations (called from UI)
    # ==================================================================

    def _queue_sync_request(self, profile_id: str) -> None:
        """Thread-safe entry point from scheduler/watcher threads."""
        self._sync_requests.put(profile_id)

    def start_sync(self, profile_id: str, interactive: bool = True) -> None:
        profile = self.profile_mgr.get(profile_id)
        if profile is None:
            return
        self.start_profile_sync(profile, interactive=interactive)

    def start_profile_sync(self, profile, interactive: bool = True) -> None:
        """Start a sync for an in-memory Profile (may or may not be saved)."""
        profile_id = profile.id
        if profile_id in self._engines and self._engines[profile_id].is_running():
            return

        validation_error = self._validate_sync_permissions(profile)
        if validation_error:
            if interactive and self._attempt_elevated_permission_fix(profile, validation_error):
                validation_error = self._validate_sync_permissions(profile)
            self._report_blocked_sync(profile_id, profile.name, validation_error, interactive)
            if validation_error:
                profile.last_sync_status = "error"
                self.profile_mgr.save(profile)
                return

        def _cb(event: SyncEvent) -> None:
            event._profile_id = profile_id  # type: ignore[attr-defined]
            self._log_event_to_file(event)
            self._event_queue.put(event)

        profile.last_sync_status = "running"
        self.profile_mgr.save(profile)

        engine = SyncEngine(profile, event_cb=_cb)
        self._engines[profile_id] = engine
        engine.start(blocking=False)

        self.navigate("monitor")

    def start_compare(self, profile_id: str) -> None:
        profile = self.profile_mgr.get(profile_id)
        if profile is None:
            return
        if profile_id in self._engines and self._engines[profile_id].is_running():
            return

        def _cb(event: SyncEvent) -> None:
            event._profile_id = profile_id  # type: ignore[attr-defined]
            self._log_event_to_file(event)
            self._event_queue.put(event)

        engine = SyncEngine(profile, event_cb=_cb, compare_only=True)
        self._engines[profile_id] = engine
        engine.start(blocking=False)
        self.navigate("monitor")

    def cancel_sync(self, profile_id: str) -> None:
        engine = self._engines.get(profile_id)
        if engine:
            engine.cancel()

    def toggle_pause_sync(self, profile_id: str) -> None:
        engine = self._engines.get(profile_id)
        if engine:
            engine.toggle_pause()

    def get_engine(self, profile_id: str) -> Optional[SyncEngine]:
        return self._engines.get(profile_id)

    def is_syncing(self, profile_id: str) -> bool:
        engine = self._engines.get(profile_id)
        return engine is not None and engine.is_running()

    # ==================================================================
    # Event pump (background threads → UI thread)
    # ==================================================================

    def _pump_events(self) -> None:
        # Process queued sync requests (from scheduler/watcher threads)
        try:
            while True:
                pid = self._sync_requests.get_nowait()
                self.start_sync(pid, interactive=False)
        except queue.Empty:
            pass

        # Process sync events
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._dispatch_event(event)
        except queue.Empty:
            pass

    def _dispatch_event(self, event: SyncEvent) -> None:
        if "monitor" in self._panels:
            panel = self._panels["monitor"]
            if hasattr(panel, "on_sync_event"):
                panel.on_sync_event(event)

        if event.kind in ("success", "error", "warning"):
            pid = getattr(event, "_profile_id", None)
            if pid:
                profile = self.profile_mgr.get(pid)
                if profile:
                    self.profile_mgr.save(profile)
            if "dashboard" in self._panels:
                panel = self._panels["dashboard"]
                if hasattr(panel, "refresh"):
                    panel.refresh()

    # ==================================================================
    # Permission helpers (ported from the tkinter app)
    # ==================================================================

    def _validate_sync_permissions(self, profile) -> Optional[str]:
        return get_delete_permission_issue(profile)

    def _attempt_elevated_permission_fix(self, profile, message: str) -> bool:
        if sys.platform != "linux" or profile.destination.type != "local":
            return False

        dst_path = profile.destination.path.strip()
        if not dst_path:
            return False

        proceed = QMessageBox.question(
            self,
            f"Admin Access Needed: {profile.name}",
            message
            + "\n\n"
            + "QueekSync can ask for administrator approval now, repair this destination folder, "
            + "and then continue the sync automatically.\n\n"
            + "Proceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if proceed != QMessageBox.StandardButton.Yes:
            return False

        success, error = self._run_elevated_permission_fix(dst_path)
        if success:
            return True

        QMessageBox.critical(
            self,
            "Permission Repair Failed",
            "QueekSync could not get administrator approval to repair the destination folder.\n\n"
            + error,
        )
        return False

    def _run_elevated_permission_fix(self, path: str) -> tuple[bool, str]:
        username = getpass.getuser()
        fix_script = (
            "import subprocess, sys; "
            "username, target = sys.argv[1], sys.argv[2]; "
            "subprocess.run(['chown', '-R', f'{username}:{username}', target], check=True); "
            "subprocess.run(['chmod', '-R', 'u+rwX', target], check=True)"
        )

        cached_sudo = ["sudo", "-n", sys.executable, "-c", fix_script, username, path]
        try:
            subprocess.run(cached_sudo, check=True, capture_output=True, text=True)
            return True, ""
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        if not shutil.which("pkexec"):
            return (
                False,
                "No graphical privilege helper is available on this system. "
                "Install polkit/pkexec or run the suggested chown/chmod commands manually.",
            )

        try:
            subprocess.run(
                ["pkexec", sys.executable, "-c", fix_script, username, path],
                check=True, capture_output=True, text=True,
            )
            return True, ""
        except FileNotFoundError:
            return False, "The pkexec command is not available on this system."
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or str(exc)).strip()
            if not details:
                details = "Administrator approval was denied or the permission repair command failed."
            return False, details

    def _report_blocked_sync(self, profile_id: str, profile_name: str, message: str, interactive: bool) -> None:
        if not message:
            return
        event = SyncEvent("error", message)
        event._profile_id = profile_id  # type: ignore[attr-defined]
        self._log_event_to_file(event)
        self._event_queue.put(event)

        if interactive:
            QMessageBox.critical(self, f"Cannot Start Sync: {profile_name}", message)

    # ==================================================================
    # Window close
    # ==================================================================

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt naming)
        cfg = self.config_mgr.config
        cfg.window_width = self.width()
        cfg.window_height = self.height()
        self.config_mgr.save()

        self._scheduler.stop()
        self._watcher_mgr.stop_all()
        try:
            if self._log_fh:
                self._log_fh.close()
        except Exception:
            pass
        event.accept()

    # ==================================================================
    # Logging
    # ==================================================================

    def get_log_file_path(self) -> str:
        return self._log_file_path

    def refresh_file_logging(self) -> None:
        self._log_file_path = self._compute_log_file_path()

        try:
            if self._log_fh:
                self._log_fh.close()
        except Exception:
            pass
        self._log_fh = None

        try:
            log_dir = Path(self._log_file_path).parent
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_fh = open(self._log_file_path, "a", encoding="utf-8")
            if os.path.getsize(self._log_file_path) == 0:
                started = Path(sys.argv[0]).name or "QueekSync"
                self._log_fh.write(f"[{Path(self._log_file_path).name}] Logging started for {started}\n")
            self._log_fh.flush()
        except Exception:
            self._log_fh = None

    @staticmethod
    def _compute_log_file_path() -> str:
        if os.name == "nt":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            log_dir = Path(base) / "QueekSync"
        else:
            base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
            log_dir = Path(base) / "QueekSync"
        return str(log_dir / "log.txt")

    def _log_event_to_file(self, event: SyncEvent) -> None:
        cfg = self.config_mgr.config
        if not self._log_fh:
            return

        level_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
        min_level = level_map.get(str(cfg.log_level).upper(), 20)
        kind_level = 20
        if event.kind == "warning":
            kind_level = 30
        elif event.kind == "error":
            kind_level = 40

        if kind_level < min_level:
            return

        pid = getattr(event, "_profile_id", "unknown")
        profile = self.profile_mgr.get(pid)
        pname = profile.name if profile else pid[:8]
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        msg = event.message.replace("\n", " ").strip()
        rel = event.rel_path.strip()
        suffix = f" | {rel}" if rel else ""
        line = f"[{ts}] [{pname}] [{event.kind.upper()}] {msg}{suffix}\n"
        try:
            self._log_fh.write(line)
            self._log_fh.flush()
        except Exception:
            pass
