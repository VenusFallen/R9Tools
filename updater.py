"""
Auto-update helpers for R9Tools (self) and LibreHardwareMonitor DLLs.

Uses only the stdlib (urllib, zipfile, json) — no extra dependencies.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import urllib.request

_APP_REPO = "VenusFallen/R9Tools"
_LHM_REPO = "LibreHardwareMonitor/LibreHardwareMonitor"
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


def _lib_dir() -> Path:
    """Persistent lib/ dir: next to the exe when frozen, next to source otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "lib"
    return Path(__file__).parent / "lib"


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
# LHM DLL update
# ---------------------------------------------------------------------------

def _lhm_version_file() -> Path:
    return _lib_dir() / "lhm_version.txt"


def installed_lhm_version() -> str:
    """Returns the installed LHM version string, or '' if not recorded."""
    f = _lhm_version_file()
    return f.read_text().strip() if f.exists() else ""


def check_lhm_update() -> tuple[bool, str]:
    """
    Returns (update_available, latest_version_str).
    Compares the latest GitHub release tag against lhm_version.txt in lib/.
    Raises on network error.
    """
    data    = _fetch_release(_LHM_REPO)
    latest  = data.get("tag_name", "").lstrip("v")
    current = installed_lhm_version()
    return (bool(latest) and latest != current), latest


def _pending_dir() -> Path:
    """Staging directory for LHM DLLs that cannot be written while loaded."""
    return _lib_dir() / "pending"


def apply_pending_lhm() -> bool:
    """
    Move staged DLLs from lib/pending/ into lib/.
    Call this at startup BEFORE _bootstrap() loads any DLLs.
    Returns True if any files were applied.
    """
    pending = _pending_dir()
    if not pending.exists():
        return False
    lib = _lib_dir()
    lib.mkdir(exist_ok=True)
    applied = False
    for src in pending.iterdir():
        dest = lib / src.name
        # Retry a few times — dest may still be locked if the previous process
        # is still shutting down (.NET releases DLL handles only after full exit).
        for attempt in range(6):
            try:
                shutil.copy2(str(src), str(dest))
                try:
                    src.unlink()
                except Exception:
                    pass
                applied = True
                break
            except PermissionError:
                if attempt < 5:
                    time.sleep(0.5)
            except Exception:
                break
    try:
        pending.rmdir()   # only removes if now empty
    except Exception:
        pass
    return applied


def _is_net10_zip(name: str) -> bool:
    """True if the asset name explicitly indicates .NET 10 (handles 'NET.10' and 'net10')."""
    stripped = name.lower().replace(".", "").replace("-", "").replace("_", "")
    return "net10" in stripped


def _compatible_zip(assets: list) -> dict | None:
    """
    From a list of release assets pick the best compatible (non-.NET 10) zip.
    If ANY asset in the release is explicitly named net10, the whole release
    targets .NET 10 (the plain zip is also net10), so return None.
    Priority: net8 explicit > net472/netfx > plain zip (no version suffix).
    """
    zips = [a for a in assets if a.get("name", "").lower().endswith(".zip")]
    # If ANY zip is explicitly net10, the entire release requires .NET 10
    if any(_is_net10_zip(a["name"]) for a in zips):
        return None
    for priority in ("net8", "net472", "netfx"):
        for a in zips:
            if priority in a["name"].lower():
                return a
    return zips[0] if zips else None


def download_lhm(progress_cb=None) -> str:
    """
    Download the latest LHM zip compatible with .NET 8/9 and stage all .dll
    files in lib/pending/.  The DLLs are applied on the next startup.
    Iterates releases newest-first until a non-.NET 10 zip is found.
    Returns the version string downloaded.  Raises on error.
    """
    url = f"https://api.github.com/repos/{_LHM_REPO}/releases?per_page=20"
    req = urllib.request.Request(url, headers={"User-Agent": "R9Tools-Updater"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        releases = json.loads(resp.read())

    asset_url = None
    chosen_version = None
    for release in releases:
        tag  = release.get("tag_name", "").lstrip("v")
        pick = _compatible_zip(release.get("assets", []))
        if pick:
            asset_url       = pick["browser_download_url"]
            chosen_version  = tag
            break

    if not asset_url:
        raise RuntimeError(
            "All recent LHM releases require .NET 10. "
            "Install .NET 10 Runtime from microsoft.com/dotnet to use the latest LHM."
        )

    payload = _download_url(asset_url, progress_cb)

    pending = _pending_dir()
    pending.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for name in zf.namelist():
            if name.lower().endswith(".dll"):
                dest = pending / Path(name).name
                dest.write_bytes(zf.read(name))

    (pending / "lhm_version.txt").write_text(chosen_version)
    return chosen_version


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
