"""
Auto-update helpers for R9Tools (self).

R9Tools ships as an Inno Setup install rather than a portable exe, so a
self-update re-runs the same installer (silently) instead of just swapping
the exe, keeping driver state, shortcuts, and the uninstall entry in sync.
Downloads the release zip, extracts R9Tools_Setup.exe, and launches it
detached so it can replace this process's own files after it quits.

Uses only the stdlib (urllib, zipfile, json) — no extra dependencies.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
import urllib.request

import crash_logging

_APP_REPO = "VenusFallen/R9Tools"
_API      = "https://api.github.com/repos/{repo}/releases/latest"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fetch_release(repo: str) -> dict:
    """Return the latest release JSON from GitHub.  Raises on network error."""
    url = _API.format(repo=repo)
    req = urllib.request.Request(url, headers={"User-Agent": "R9Tools-Updater"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _download_url(url: str, progress_cb=None) -> bytes:
    """Download a URL to memory.  progress_cb(pct: int) is optional."""
    req = urllib.request.Request(url, headers={"User-Agent": "R9Tools-Updater"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total      = int(resp.headers.get("Content-Length", 0))
        buf        = io.BytesIO()
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.write(chunk)
            downloaded += len(chunk)
            if progress_cb and total:
                progress_cb(int(downloaded * 100 / total))
    if progress_cb:
        progress_cb(100)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# R9Tools self-update
# ---------------------------------------------------------------------------

def check_app_update(current_version: str) -> tuple[bool, str]:
    """
    Returns (update_available, latest_version_str).
    Raises on network error.
    """
    data   = _fetch_release(_APP_REPO)
    latest = data.get("tag_name", "").lstrip("v")
    cur    = current_version.lstrip("v")
    return (bool(latest) and latest != cur), latest


def _find_zip_asset_url(data: dict) -> str:
    """
    Pick the release zip asset. Released builds publish exactly one asset,
    named like ``R9Tools_v1.1.1.zip`` — match on both the ``.zip``
    extension and the ``R9Tools_v`` name prefix so this doesn't accidentally
    grab an unrelated asset if the release ever ships more than one.
    """
    assets = data.get("assets", [])
    for a in assets:
        name = a.get("name", "")
        if name.lower().endswith(".zip") and name.lower().startswith("r9tools_v"):
            return a["browser_download_url"]
    # Fall back to any .zip asset if the naming convention ever changes,
    # rather than hard-failing on a cosmetic mismatch.
    for a in assets:
        if a.get("name", "").lower().endswith(".zip"):
            return a["browser_download_url"]
    raise RuntimeError("No R9Tools_v*.zip asset found in the latest R9Tools release")


def download_app(progress_cb=None) -> Path:
    """
    Download the latest R9Tools release zip and extract R9Tools_Setup.exe
    from it into a fresh temp directory.

    Only works when frozen (PyInstaller --onefile).  Raises otherwise.

    Returns the path to the extracted R9Tools_Setup.exe.  Nothing is
    executed here — call launch_installer_and_quit() with the returned path
    once the caller is ready to hand off to the installer.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only works in the packaged exe")

    data      = _fetch_release(_APP_REPO)
    asset_url = _find_zip_asset_url(data)

    payload = _download_url(asset_url, progress_cb)

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        installer_member = next(
            (n for n in zf.namelist()
             if Path(n).name.lower() == "r9tools_setup.exe"),
            None,
        )
        if installer_member is None:
            raise RuntimeError(
                "R9Tools_Setup.exe not found inside the downloaded release zip"
            )

        extract_dir = Path(tempfile.mkdtemp(prefix="r9tools_update_"))
        extracted_path = extract_dir / "R9Tools_Setup.exe"
        with zf.open(installer_member) as src, open(extracted_path, "wb") as dst:
            dst.write(src.read())

    return extracted_path


# ---------------------------------------------------------------------------
# Installer handoff
# ---------------------------------------------------------------------------

def _quote_ps_single(value: str) -> str:
    """
    Wrap ``value`` in single quotes for safe embedding in a PowerShell
    -Command string, doubling any embedded single quotes (PowerShell's
    single-quoted-string escape rule) so paths with apostrophes still
    round-trip as one literal token.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _build_relaunch_command(pid: int, installer_path: Path, install_args: list[str]) -> str:
    """
    Build the PowerShell -Command string that waits for the process ``pid``
    to fully exit, then starts ``installer_path`` with ``install_args``.

    Split out as its own pure function (no subprocess spawning) so the
    generated command text can be unit-tested directly -- see
    tests/test_updater_relaunch_command.py.
    """
    arg_list = ", ".join(_quote_ps_single(a) for a in install_args)
    return (
        f"Wait-Process -Id {pid} -Timeout 30 -ErrorAction SilentlyContinue; "
        f"Start-Process -FilePath {_quote_ps_single(str(installer_path))} "
        f"-ArgumentList @({arg_list}) -WindowStyle Hidden"
    )


def launch_installer_and_quit(installer_path: Path) -> None:
    """
    Hand off to the extracted R9Tools_Setup.exe, then return so the caller
    can quit the app.

    This does NOT launch the installer directly. It spawns a detached
    PowerShell watcher that first runs ``Wait-Process -Id <this pid>``
    (captured via os.getpid() before any quitting happens, with a 30s
    safety-net timeout in case shutdown ever hangs) and only starts the
    installer once that resolves -- i.e. once this process has actually,
    fully terminated, not merely been asked to via QApplication.quit().

    Why: a prior fix relied solely on R9Tools.iss's
    `CloseApplications=yes` + `AppMutex=R9Tools_AppMutex` (Restart Manager
    closing this process via the mutex main.py's _create_app_mutex() holds)
    to make the race safe. A real failed-update Inno Setup log showed that
    assumption was wrong: Setup's classic AppMutex check aborts within
    milliseconds of launch if it still sees the mutex, faster than any
    RM-mediated close-and-wait could complete, so /SUPPRESSMSGBOXES just
    auto-picked Cancel and the whole install silently aborted before
    ever copying files (see project history for the log). Guaranteeing
    real process death before Setup even starts removes the race instead
    of trying to win it. R9Tools.iss's AppMutex/CloseApplications setup is
    kept as a defense-in-depth safety net for any other stray process
    holding the mutex, not as the primary mechanism anymore.

    The caller should still quit the running app (QApplication.instance()
    .quit() or equivalent) immediately after this returns, same as before
    -- that's what the watcher above is waiting on.

    Flags (Inno Setup command-line silent-install switches):
      /VERYSILENT          - no wizard UI at all; the "Installing..."
                              progress window itself is hidden too
                              (plain /SILENT still shows a progress window)
      /SUPPRESSMSGBOXES    - suppress the few informational message boxes
                              Setup can otherwise show during a silent run;
                              combined with CloseApplications=yes above,
                              Setup closes the running app automatically
                              rather than showing a "close this running
                              application?" prompt no one is there to click
      /NORESTART            - never prompt for or force a reboot even if a
                              restart would normally be suggested
      /LOG=<path>            - write a full Inno Setup install log to a
                              timestamped file under the same
                              %LOCALAPPDATA%\\R9Tools\\logs directory
                              crash_logging.py uses for the app's own log,
                              so a silent update attempt (successful or
                              not) leaves a real diagnostic trail instead
                              of none -- the [Code] section's own Log()
                              calls (e.g. RelaunchAppAfterSilentUpdate's
                              retry attempts) only ever reach this file;
                              without /LOG they're discarded entirely

    The PowerShell watcher is started detached from this process (new
    process group, breakaway from any job object this process might be
    part of) so it survives this process exiting rather than being torn
    down with it, and the installer it eventually launches inherits that
    same detachment via Start-Process.
    """
    if sys.platform != "win32":
        raise RuntimeError("Installer handoff is only supported on Windows")

    installer_path = Path(installer_path)
    if not installer_path.is_file():
        raise RuntimeError(f"Installer not found at {installer_path}")

    my_pid = os.getpid()

    creationflags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_BREAKAWAY_FROM_JOB
    )

    log_path = None
    try:
        log_dir = Path(crash_logging.get_log_dir())
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"update_install_{timestamp}.log"
    except Exception:
        # Best-effort only -- a log path we couldn't prepare must never
        # block the actual update install from proceeding.
        log_path = None

    install_args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    if log_path is not None:
        install_args.append(f"/LOG={log_path}")

    ps_command = _build_relaunch_command(my_pid, installer_path, install_args)

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-Command", ps_command,
        ],
        creationflags=creationflags,
        close_fds=True,
    )
