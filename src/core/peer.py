
"""
Peer sync support.

Lets the user connect to another computer over SSH (SFTP), browse folders
on both machines, and sync the folders they select. The heavy lifting is
done by the existing sync engine; this module only manages the remote
connection, folder listing, and per-pair status comparison.
"""

from __future__ import annotations

import json
import os
import stat as stat_mod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.profile import EndpointConfig, FilterConfig, Profile, SyncOptions
from core.syncer import FileInfo, LocalFS, SFTPFS


DEFAULT_EXCLUDES: List[str] = list(FilterConfig().exclude_patterns)

# Name of the favourites file each computer keeps in its home directory.
# It lives in the home dir so the *other* computer can read it over SFTP
# and show these folders immediately when connecting.
FAVORITES_FILE_NAME = ".queeksync-favorites.json"


def favorites_file_path() -> str:
    """Absolute path of this computer's favourites file."""
    return os.path.join(os.path.expanduser("~"), FAVORITES_FILE_NAME)


def load_local_favorites() -> List[str]:
    """Load this computer's favourite folder paths (may be empty)."""
    try:
        with open(favorites_file_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        favs = data.get("favorites", []) if isinstance(data, dict) else []
        return [f for f in favs if isinstance(f, str)]
    except Exception:
        return []


def save_local_favorites(paths: List[str]) -> None:
    """Persist this computer's favourite folder paths."""
    cleaned = sorted({os.path.normpath(p) for p in paths if p})
    data = {"version": 1, "favorites": cleaned}
    tmp = favorites_file_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, favorites_file_path())


def add_local_favorite(path: str) -> bool:
    """Add a favourite folder. Returns True when newly added."""
    norm = os.path.normpath(path)
    favs = load_local_favorites()
    if norm in favs:
        return False
    favs.append(norm)
    save_local_favorites(favs)
    return True


def remove_local_favorite(path: str) -> bool:
    """Remove a favourite folder. Returns True when it was present."""
    norm = os.path.normpath(path)
    favs = load_local_favorites()
    if norm not in favs:
        return False
    favs.remove(norm)
    save_local_favorites(favs)
    return True


def read_remote_favorites(peer: "PeerConnection") -> List[str]:
    """Read the other computer's favourite folders over SFTP (empty if none)."""
    try:
        remote_path = f"{peer.home_dir.rstrip('/')}/{FAVORITES_FILE_NAME}"
        with peer.sftp.open(remote_path, "r") as fh:
            data = json.load(fh)
        favs = data.get("favorites", []) if isinstance(data, dict) else []
        return [f for f in favs if isinstance(f, str)]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Remote connection
# ---------------------------------------------------------------------------

class PeerConnection:
    """An SSH/SFTP connection to another computer."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        key_file: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_file = key_file
        self._ssh = None
        self._sftp = None
        self.home_dir: str = ""

    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._sftp is not None

    @property
    def sftp(self):
        """The live paramiko SFTP client (owned by this connection)."""
        return self._sftp

    def connect(self) -> str:
        """Open the SSH/SFTP connection. Returns the remote home directory."""
        import paramiko  # type: ignore[import]

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password or None,
            key_filename=os.path.expanduser(self.key_file) if self.key_file else None,
            timeout=10,
        )
        self._ssh = client
        self._sftp = client.open_sftp()

        home = ""
        try:
            _stdin, stdout, _stderr = client.exec_command("printf '%s' \"$HOME\"", timeout=10)
            home = stdout.read().decode("utf-8", "replace").strip()
        except Exception:
            home = ""
        if not home:
            home = f"/home/{self.username}" if self.username else "/"
        self.home_dir = home
        return home

    def disconnect(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
        if self._ssh is not None:
            try:
                self._ssh.close()
            except Exception:
                pass
        self._sftp = None
        self._ssh = None
        self.home_dir = ""

    # ------------------------------------------------------------------

    def list_folders(self, path: str) -> List[dict]:
        """List top-level directories under ``path`` on the remote machine.

        Returns a list of ``{"name", "mtime", "size"}`` dicts sorted by name.
        Raises FileNotFoundError when the remote path does not exist.
        """
        folders: List[dict] = []
        entries = self._sftp.listdir_attr(path)
        for attr in entries:
            if stat_mod.S_ISDIR(attr.st_mode):
                folders.append(
                    {
                        "name": attr.filename,
                        "mtime": float(attr.st_mtime or 0),
                        "size": int(attr.st_size or 0),
                    }
                )
        folders.sort(key=lambda d: d["name"].lower())
        return folders

    def make_endpoint(self, path: str) -> EndpointConfig:
        return EndpointConfig(
            type="sftp",
            path=path,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            key_file=self.key_file,
        )


# ---------------------------------------------------------------------------
# Local folder listing
# ---------------------------------------------------------------------------

def list_local_folders(path: str) -> List[dict]:
    """List top-level directories under a local path.

    Returns the same shape as :meth:`PeerConnection.list_folders`.
    Raises FileNotFoundError when the path does not exist.
    """
    folders: List[dict] = []
    with os.scandir(path) as it:
        for entry in it:
            try:
                if entry.is_dir(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    folders.append({"name": entry.name, "mtime": st.st_mtime, "size": 0})
            except OSError:
                continue
    folders.sort(key=lambda d: d["name"].lower())
    return folders


# ---------------------------------------------------------------------------
# Per-folder scan results
# ---------------------------------------------------------------------------

@dataclass
class FolderScan:
    """Summary of one folder (local or remote)."""

    name: str
    path: str
    file_count: int = 0
    latest_mtime: float = 0.0
    total_size: int = 0
    error: str = ""
    missing: bool = False     # folder does not exist on that side

    def summary_line(self) -> str:
        if self.missing:
            return "not present"
        if self.error:
            return f"error: {self.error}"
        from datetime import datetime
        when = datetime.fromtimestamp(self.latest_mtime).strftime("%Y-%m-%d %H:%M") if self.latest_mtime else "never"
        return f"{self.file_count} file(s), latest {when}"


def _summarize(name: str, path: str, files: List[FileInfo]) -> FolderScan:
    files_only = [f for f in files if not f.is_dir]
    latest = max((f.mtime for f in files_only), default=0.0)
    total = sum(f.size for f in files_only)
    return FolderScan(
        name=name,
        path=path,
        file_count=len(files_only),
        latest_mtime=latest,
        total_size=total,
    )


def _scan_local_files(path: str, exclude: Optional[List[str]]) -> Tuple[List[FileInfo], str, bool]:
    """Scan a local folder. Returns (files, error, missing)."""
    try:
        files = LocalFS.scan(path, [], exclude if exclude is not None else DEFAULT_EXCLUDES)
        return files, "", False
    except FileNotFoundError:
        return [], "", True
    except Exception as exc:
        return [], str(exc), False


def _scan_remote_files(peer: PeerConnection, path: str, exclude: Optional[List[str]]) -> Tuple[List[FileInfo], str, bool]:
    """Scan a remote folder over the peer's SFTP connection. Returns (files, error, missing)."""
    fs = SFTPFS(
        peer.host,
        peer.port,
        peer.username,
        peer.password,
        peer.key_file,
        sftp=peer.sftp,
    )
    try:
        files = fs.scan(path, [], exclude if exclude is not None else DEFAULT_EXCLUDES)
        return files, "", False
    except FileNotFoundError:
        return [], "", True
    except Exception as exc:
        return [], str(exc), False


def scan_local_folder(path: str, name: str, exclude: Optional[List[str]] = None) -> FolderScan:
    """Recursively scan a local folder (respecting default excludes)."""
    files, error, missing = _scan_local_files(path, exclude)
    if missing:
        return FolderScan(name=name, path=path, missing=True)
    if error:
        return FolderScan(name=name, path=path, error=error)
    return _summarize(name, path, files)


def scan_remote_folder(peer: PeerConnection, path: str, name: str, exclude: Optional[List[str]] = None) -> FolderScan:
    """Recursively scan a remote folder over the peer's SFTP connection."""
    files, error, missing = _scan_remote_files(peer, path, exclude)
    if missing:
        return FolderScan(name=name, path=path, missing=True)
    if error:
        return FolderScan(name=name, path=path, error=error)
    return _summarize(name, path, files)


def compare_pair(
    peer: PeerConnection,
    local_path: str,
    remote_path: str,
    name: str,
    exclude: Optional[List[str]] = None,
) -> PairCompare:
    """Scan both sides of one folder pair and produce a full comparison.

    The returned :class:`PairCompare` has the per-file diff counters filled
    in, so its ``verdict`` reflects which side is actually newer.
    """
    local_files, local_error, local_missing = _scan_local_files(local_path, exclude)
    remote_files, remote_error, remote_missing = _scan_remote_files(peer, remote_path, exclude)

    local = FolderScan(name=name, path=local_path)
    if local_missing:
        local.missing = True
    elif local_error:
        local.error = local_error
    else:
        local = _summarize(name, local_path, local_files)

    remote = FolderScan(name=name, path=remote_path)
    if remote_missing:
        remote.missing = True
    elif remote_error:
        remote.error = remote_error
    else:
        remote = _summarize(name, remote_path, remote_files)

    cmp = compare_folders(name, local, remote)
    if not (local.error or local.missing or remote.error or remote.missing):
        (
            cmp.local_newer_files,
            cmp.remote_newer_files,
            cmp.same_files,
            cmp.only_local_files,
            cmp.only_remote_files,
        ) = diff_file_maps(local_files, remote_files)
    return cmp


# ---------------------------------------------------------------------------
# Pair comparison (which side is newer?)
# ---------------------------------------------------------------------------

@dataclass
class PairCompare:
    """Result of comparing one local folder with one remote folder."""

    name: str
    local: FolderScan
    remote: FolderScan
    local_newer_files: int = 0
    remote_newer_files: int = 0
    same_files: int = 0
    only_local_files: int = 0
    only_remote_files: int = 0

    # ------------------------------------------------------------------

    @property
    def verdict(self) -> str:
        """One of: error, empty, only_local, only_remote, in_sync,
        local_newer, remote_newer, mixed."""
        if self.local.missing and self.remote.missing:
            return "empty"
        if self.local.missing:
            return "only_remote"
        if self.remote.missing:
            return "only_local"
        if self.local.error or self.remote.error:
            return "error"
        if self.local.file_count == 0 and self.remote.file_count == 0:
            return "empty"
        if self.local.file_count > 0 and self.remote.file_count == 0:
            return "only_local"
        if self.local.file_count == 0 and self.remote.file_count > 0:
            return "only_remote"

        if (
            self.local_newer_files == 0
            and self.remote_newer_files == 0
            and self.only_local_files == 0
            and self.only_remote_files == 0
        ):
            return "in_sync"

        local_score = self.local_newer_files + self.only_local_files
        remote_score = self.remote_newer_files + self.only_remote_files
        if local_score > remote_score:
            return "local_newer"
        if remote_score > local_score:
            return "remote_newer"
        return "mixed"

    @property
    def sync_action(self) -> str:
        v = self.verdict
        if v == "only_local":
            return "copy to other PC"
        if v == "only_remote":
            return "copy to this PC"
        if v in ("in_sync", "empty"):
            return "nothing to do"
        if v == "error":
            return "blocked"
        return "two-way sync"

    def detail_text(self) -> str:
        parts = [
            f"{self.same_files} in sync",
            f"{self.local_newer_files} newer on this PC",
            f"{self.remote_newer_files} newer on other PC",
        ]
        if self.only_local_files:
            parts.append(f"{self.only_local_files} only on this PC")
        if self.only_remote_files:
            parts.append(f"{self.only_remote_files} only on other PC")
        return "  ·  ".join(parts)


def compare_folders(name: str, local: FolderScan, remote: FolderScan) -> PairCompare:
    """Compare two already-scanned folders file by file.

    The caller is responsible for scanning both sides; this function only
    needs the two FolderScan summaries (mtime/size aggregates are used for
    the verdict when a full file diff is unavailable, otherwise the caller
    may fill in the diff counters directly).
    """
    return PairCompare(name=name, local=local, remote=remote)


def diff_file_maps(
    local_files: List[FileInfo],
    remote_files: List[FileInfo],
) -> Tuple[int, int, int, int, int]:
    """Diff two file lists and return
    (local_newer, remote_newer, same, only_local, only_remote).

    A file is "newer" when its size differs or its mtime differs by more
    than 2 seconds (same heuristic as the sync engine).
    """
    local_map: Dict[str, FileInfo] = {f.rel_path: f for f in local_files if not f.is_dir}
    remote_map: Dict[str, FileInfo] = {f.rel_path: f for f in remote_files if not f.is_dir}

    local_newer = 0
    remote_newer = 0
    same = 0
    only_local = 0
    only_remote = 0

    for rel in set(local_map) | set(remote_map):
        lf = local_map.get(rel)
        rf = remote_map.get(rel)
        if lf is None:
            only_remote += 1
        elif rf is None:
            only_local += 1
        elif lf.size == rf.size and abs(lf.mtime - rf.mtime) <= 2:
            same += 1
        elif lf.mtime > rf.mtime + 2:
            local_newer += 1
        elif rf.mtime > lf.mtime + 2:
            remote_newer += 1
        elif lf.size != rf.size:
            # Same timestamp, different content: prefer the larger side
            if lf.size > rf.size:
                local_newer += 1
            else:
                remote_newer += 1
        else:
            same += 1

    return local_newer, remote_newer, same, only_local, only_remote


# ---------------------------------------------------------------------------
# Profile construction for a selected pair
# ---------------------------------------------------------------------------

def build_peer_profile(
    folder_name: str,
    local_path: str,
    remote_cfg: EndpointConfig,
    mode: str = "two_way",
    host_label: str = "",
    remote_path: str = "",
    direction: str = "local_to_remote",
) -> Profile:
    """Build a Profile that syncs one local folder with one remote folder.

    ``direction`` is "local_to_remote" (or "two_way", where local is the
    source) or "remote_to_local" (for folders that only exist remotely).
    """
    label = f"Peer: {folder_name}" + (f" ({host_label})" if host_label else "")
    p = Profile(name=label, color="#8b5cf6")
    p.description = (
        f"Peer sync of '{folder_name}' between this computer and "
        f"{remote_cfg.username}@{remote_cfg.host}"
    )
    remote = EndpointConfig.from_dict(remote_cfg.to_dict())
    if remote_path:
        remote.path = remote_path
    if direction == "remote_to_local":
        p.source = remote
        p.destination = EndpointConfig(type="local", path=local_path)
    else:
        p.source = EndpointConfig(type="local", path=local_path)
        p.destination = remote
    p.options = SyncOptions(mode=mode, preserve_timestamps=True, delete_extra=False)
    return p


def _match_orientation(profile: Profile, local_path: str, remote_cfg: EndpointConfig) -> bool:
    """True when a profile syncs the given local folder with the given remote folder,
    regardless of which endpoint is the source."""
    local_norm = os.path.normpath(os.path.expanduser(local_path))
    remote_path = remote_cfg.path.rstrip("/")
    s, d = profile.source, profile.destination

    if s.type == "local" and d.type == "sftp":
        return (
            os.path.normpath(os.path.expanduser(s.path)) == local_norm
            and d.host == remote_cfg.host
            and d.port == remote_cfg.port
            and d.username == remote_cfg.username
            and d.path.rstrip("/") == remote_path
        )
    if s.type == "sftp" and d.type == "local":
        return (
            os.path.normpath(os.path.expanduser(d.path)) == local_norm
            and s.host == remote_cfg.host
            and s.port == remote_cfg.port
            and s.username == remote_cfg.username
            and s.path.rstrip("/") == remote_path
        )
    return False


def find_peer_profile(profile_mgr, local_path: str, remote_cfg: EndpointConfig) -> Optional[Profile]:
    """Find an existing profile for the same local folder + remote folder pair.

    Re-using the profile keeps the Profiles page tidy across repeated
    peer syncs and lets the user schedule the pair later.
    """
    for p in profile_mgr.all():
        if _match_orientation(p, local_path, remote_cfg):
            return p
    return None


def find_or_create_peer_profile(profile_mgr, profile: Profile) -> Profile:
    """Return the existing profile for this pair (updated), or save and return ``profile``."""
    if profile.source.type == "local":
        local_path, remote_cfg = profile.source.path, profile.destination
    else:
        local_path, remote_cfg = profile.destination.path, profile.source

    existing = find_peer_profile(profile_mgr, local_path, remote_cfg)
    if existing is not None:
        existing.name = profile.name
        existing.description = profile.description
        existing.color = profile.color
        existing.source = profile.source
        existing.destination = profile.destination
        existing.options = profile.options
        return existing
    profile_mgr.save(profile)
    return profile


# ---------------------------------------------------------------------------
# Pair plan (what the user has selected)
# ---------------------------------------------------------------------------

@dataclass
class PeerPlan:
    """One selected sync pair from the peer panel."""

    name: str
    key: str = ""            # unique row key (path-based)
    local_path: str = ""
    remote_path: str = ""
    local_checked: bool = False
    remote_checked: bool = False
    compare: Optional[PairCompare] = None

    @property
    def both_sides(self) -> bool:
        return self.local_checked and self.remote_checked

    @property
    def mode(self) -> str:
        return "two_way" if self.both_sides else "one_way"

    @property
    def direction(self) -> str:
        if self.both_sides:
            return "two-way"
        if self.local_checked:
            return "this PC → other PC"
        return "other PC → this PC"

    def build_profile(self, local_root: str, remote_root: str, remote_cfg: EndpointConfig, host_label: str) -> Profile:
        if self.local_checked:
            local_path = self.local_path or os.path.join(local_root, self.name)
        else:
            local_path = os.path.join(local_root, self.name)
        if self.remote_checked:
            remote_path = self.remote_path or f"{remote_root.rstrip('/')}/{self.name}"
        else:
            remote_path = f"{remote_root.rstrip('/')}/{self.name}"
        direction = "remote_to_local" if self.remote_checked and not self.local_checked else "local_to_remote"
        return build_peer_profile(
            self.name,
            local_path,
            remote_cfg,
            mode=self.mode,
            host_label=host_label,
            remote_path=remote_path,
            direction=direction,
        )
