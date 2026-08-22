"""
Auto-update helpers for R9Tools (self).

LibreHardwareMonitor's DLLs are bundled directly in lib/ and ship with every
R9Tools release — there is no separate runtime download/update path for them.

Uses only the stdlib (urllib, zipfile, json) — no extra dependencies.
"""
import io
import json
import subprocess
import sys
from pathlib import Path
import urllib.request

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


def download_app(progress_cb=None) -> None:
    """
    Download the latest R9Tools.exe and replace the running executable.
    Only works when frozen (PyInstaller --onefile).  Raises otherwise.
    The replaced exe takes effect after restart_app() is called.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Self-update only works in the packaged exe")

    data = _fetch_release(_APP_REPO)
    asset_url = next(
        (a["browser_download_url"] for a in data.get("assets", [])
         if a.get("name", "").lower().endswith(".exe")),
        None,
    )
    if not asset_url:
        raise RuntimeError("No .exe asset found in the latest R9Tools release")

    payload  = _download_url(asset_url, progress_cb)
    exe_path = Path(sys.executable)
    tmp_path = exe_path.with_suffix(".new.exe")
    old_path = exe_path.with_suffix(".old.exe")

    tmp_path.write_bytes(payload)
    if old_path.exists():
        old_path.unlink()
    exe_path.rename(old_path)
    tmp_path.rename(exe_path)


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

def restart_app() -> None:
    """
    Launch a fresh instance of the app.
    The caller should call QApplication.instance().quit() immediately after.
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        # Open a new console window so the new process doesn't share (and
        # lock up) the parent's terminal.
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    if getattr(sys, "frozen", False):
        # Frozen exe: sys.executable is the exe; pass any extra argv flags
        args = [sys.executable] + sys.argv[1:]
    else:
        # Dev: sys.executable is python.exe; sys.argv[0] is the script path
        args = [sys.executable] + sys.argv
    subprocess.Popen(args, **kwargs)
