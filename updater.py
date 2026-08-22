"""
Auto-update helpers for R9Tools (self).

R9Tools ships as a proper Inno Setup install (Program Files, Interception
driver, Start Menu shortcuts, uninstall registry entry) rather than a
portable single-exe app, so a self-update has to go through the same
installer a fresh install does — that's the only path that keeps driver
state, shortcuts, and the uninstall entry correctly in sync. This module:

  1. Downloads the release zip asset (``R9Tools_v<version>.zip``) from the
     latest GitHub release and extracts ``R9Tools_Setup.exe`` from it.
  2. Launches that installer as a detached, silent (unattended, no wizard
     UI, no reboot prompt) subprocess, independent of this process's
     lifetime, and lets the caller quit the running app immediately after
     so the installer's file-replace of the currently-installed
     R9Tools.exe can proceed without a file lock in the way.

LibreHardwareMonitor's DLLs are bundled directly in lib/ and ship with every
R9Tools release — there is no separate runtime download/update path for them.

Uses only the stdlib (urllib, zipfile, json) — no extra dependencies.
"""
import io
import json
import logging
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
import urllib.request

_APP_REPO = "VenusFallen/R9Tools"
_API      = "https://api.github.com/repos/{repo}/releases/latest"

_logger = logging.getLogger("r9tools.updater")


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

def launch_installer_and_quit(installer_path: Path) -> None:
    """
    Launch the extracted R9Tools_Setup.exe as a fully detached, independent
    process performing a silent, unattended update-install, then return.

    The caller should quit the running app (QApplication.instance().quit()
    or equivalent) immediately after this returns, so this process's file
    lock on the currently-installed R9Tools.exe releases and the installer
    can replace it.

    Flags (Inno Setup command-line silent-install switches — verified
    empirically against the actual compiled R9Tools_Setup.exe pointed at a
    throwaway /DIR=, not just assumed from memory):
      /VERYSILENT          - no wizard UI at all; the "Installing..."
                              progress window itself is hidden too
                              (plain /SILENT still shows a progress window)
      /SUPPRESSMSGBOXES    - suppress the few informational message boxes
                              Setup can otherwise show (e.g. "close this
                              running application?") during a silent run
      /NORESTART            - never prompt for or force a reboot even if a
                              restart would normally be suggested

    The subprocess is started detached from this process (new process
    group, breakaway from any job object this process might be part of) so
    it survives this process exiting rather than being torn down with it.
    """
    if sys.platform != "win32":
        raise RuntimeError("Installer handoff is only supported on Windows")

    installer_path = Path(installer_path)
    if not installer_path.is_file():
        raise RuntimeError(f"Installer not found at {installer_path}")

    creationflags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_BREAKAWAY_FROM_JOB
    )

    subprocess.Popen(
        [str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        creationflags=creationflags,
        close_fds=True,
    )
