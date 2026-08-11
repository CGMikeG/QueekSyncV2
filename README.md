# QueekSync

QueekSync is a cross-platform desktop file synchronization app with a modern GUI.
It helps you sync files between:

- Local folder to local folder
- Local folder to SFTP server
- SFTP server to local folder
- SFTP server to SFTP server

## Purpose

Use QueekSync when you want repeatable, profile-based sync jobs without writing shell scripts.
Each sync profile stores source/destination endpoints, sync behavior, filters, and optional scheduling.

## Key Features

- Profile-based sync setup
- Sync modes:
  - one_way: copy source to destination only
  - mirror: one_way plus delete files missing from source
  - two_way: bidirectional sync based on newer timestamps
- Include and exclude patterns
- Optional checksum verification
- Optional bandwidth limit
- Scheduled auto-sync
- File-change triggered sync for local source folders
- Live monitor panel with progress and logs
- SFTP connection testing and remote folder browser
- Peer Sync: browse folders on two computers side by side over SSH, compare which side is newer, and sync selected folders

## Tech Stack

- Python 3.10+
- customtkinter (GUI)
- paramiko (SFTP)
- watchdog (filesystem watching)
- schedule (interval scheduling)

## Project Structure

- main.py: app entry point
- src/core/: sync engine, profiles, scheduler, watcher
- src/ui/: desktop UI panels and dialogs
- run.bat: Windows launcher
- run.sh: Linux/WSL launcher

## Requirements

- Python 3.10 or newer
- Network access to any SFTP targets you want to sync
- On Linux/WSL: working GUI display support

## Installation and Launch

### Windows (recommended)

Double-click run.bat

What happens:

1. Creates .venv on first run
2. Installs dependencies from requirements.txt into the local .venv
3. Verifies that the Windows Python installation includes tkinter/Tcl-Tk support
4. Launches the app

Or run manually from terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

Running `main.py` directly also self-bootstraps into the project-local `.venv` if needed, so the app does not continue under the system Python environment.
If `requirements.txt` changes later, QueekSync re-syncs the local `.venv` automatically on the next launch.

### Linux or WSL

Run the one-time setup first - it creates the project virtual environment,
installs all dependencies from requirements.txt, verifies the app can start,
and prints next steps (safe to re-run; re-installs only when requirements
change):

```bash
chmod +x setup.sh
./setup.sh
```

Then launch the app:

```bash
chmod +x run_qt.sh
./run_qt.sh        # PyQt6 UI (recommended)
# or: ./run.sh     # legacy customtkinter UI
```

The launcher uses the app's own virtual environment (`.venv`): on a new
computer it creates it and installs all dependencies automatically, so
`./run_qt.sh` alone is enough to get started. `setup.sh` does the same thing
up front and also verifies the app can start.

What happens:

1. Creates .venv on first run
2. Installs dependencies from requirements.txt into the local .venv
3. If tkinter is missing, attempts to install the required system Tk package using your distro package manager
4. Launches the app and writes logs to ~/.local/share/QueekSync/queeksync.log

Notes:

- Automatic Tk installation may prompt for sudo/root access
- If auto-install is not available for your distro, the launcher prints the manual package command to run

## How to Use

### 0. Peer Sync (between two computers)

If two computers both run QueekSync, you can sync folders between them over
SSH using just the other computer's IP address and password:

1. Open the **Peer Sync** page from the sidebar.
2. Enter the other computer's IP (or hostname), SSH port, username, and password, then click **Connect**. Click **Save current connection** to store the details (with an optional remembered password). Saved connections appear in the **Saved connections** panel at the top of the page - select one and click **Load** (or double-click it) to connect without re-entering the details.
3. Both computers' folders are shown side by side (defaults to the home folders; you can change the base folder on either side).
4. Tick the folders you want to sync — folders present on both sides are paired by name, folders checked on only one side can be copied to the other.
5. Click **Compare Selected** to see which side is newer for each folder (file counts, latest change, and a verdict: This PC newer / Other PC newer / In sync / Only on one side).
6. Click **Sync Selected** to run the syncs. Shared folders sync two-way; one-sided folders are copied to the missing side. Progress appears in the Monitor panel.

**Sync direction**: the Peer Sync page has a direction selector - **Two-way
(automatic)** (default: folders on both computers sync both ways, one-sided
folders are copied across), **This PC → Other PC**, or **Other PC → This PC**
(one-way copies always go that direction). With a one-way direction you can
also tick **Delete extra files at destination** to mirror: files at the
destination that no longer exist in the source are removed.

**Favourites** (★) and **sync list** (⇄) work on the **base folder**: set
"This computer – base folder" to the folder you want (or Browse to it), then
click **★ Favourite** / **⇄ Sync List** next to it - no need to select a
folder from the list first. Favourites are stored in
`~/.queeksync-favorites.json`, the sync list in `~/.queeksync-sync-list.json`
(both in the home folder), and the other computer downloads them
automatically when it connects. Click **⇄ Sync Remote List** to download the
other computer's list and sync every folder on it in one click (folders that
exist on both computers sync two-way, one-sided folders are copied across).

Each pair is saved as a profile (named `Peer: <folder>`), so it can be re-run
or scheduled later; re-syncing the same pair reuses the existing profile
instead of creating duplicates. Passwords are stored in the profile files in
plaintext, matching the existing SFTP profile behaviour.

### 1. Create a Sync Profile

1. Open QueekSync
2. Go to Profiles (or use New Profile from Dashboard)
3. Enter profile name and optional description
4. Configure Source endpoint
5. Configure Destination endpoint
6. Save profile

Endpoint types:

- local: local filesystem path
- sftp: host, port, username, password and/or key file, remote path

### 2. Choose Sync Behavior

In the profile editor:

- Select mode: one_way, mirror, or two_way
- Enable delete extra files only if you are sure
- Configure include and exclude patterns
- Optionally enable checksum verification for safer comparisons

### 3. Run a Sync

- Click Sync on a profile card/row
- App switches to Monitor panel
- Watch progress, copied/deleted files, warnings, and errors

### 4. Enable Automation (Optional)

For each profile, you can enable schedule settings:

- Interval-based sync (every N minutes)
- Auto trigger when local source files change

## Data and Config Storage

QueekSync stores app settings and profiles outside the repository.

Windows:

- Config: %APPDATA%/QueekSync/config.json
- Profiles: %APPDATA%/QueekSync/profiles/*.json

Linux:

- Config: ~/.config/QueekSync/config.json
- Profiles: ~/.config/QueekSync/profiles/*.json

## Security Notes

- Profile SFTP passwords are stored in profile JSON in plaintext.
- Prefer SSH key authentication whenever possible.
- Do not share exported profile files if they contain credentials.

## Common Workflow Example

1. Create profile named Website Backup
2. Source: local folder C:/sites/myapp
3. Destination: SFTP folder /backups/myapp
4. Mode: one_way
5. Exclude: .git, __pycache__, *.log
6. Test SFTP connection
7. Save profile and run Sync
8. Enable schedule to run every 60 minutes

## Troubleshooting

- App does not launch on Windows:
  - Verify Python 3.10+ is installed and available in PATH
- Windows launcher reports missing tkinter:
  - Repair or reinstall Python 3.10+ and ensure the Tcl/Tk and IDLE feature is included
- SFTP connection fails:
  - Check host, port, username, credentials, firewall
- WSL GUI issues:
  - Ensure WSLg or an X server is configured
- Linux launcher exits with missing tkinter:
  - Install the Tk package for your distro, commonly python3-tk or python3-tkinter
- Mirror or delete-extra sync fails on Linux:
  - The destination folder must be writable and executable by your user because Linux delete permissions are controlled by the parent directory
  - Manual sync now offers a one-time admin approval prompt to repair the destination folder and continue automatically when `pkexec` is available
  - Prefer fixing the folder with `chown` or `chmod u+rwX` instead of running the whole app with sudo
- Dependency errors:
  - Reinstall from requirements.txt inside the project virtual environment

## Development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run app:

```bash
python main.py
```

## License

Add your preferred license details here.
