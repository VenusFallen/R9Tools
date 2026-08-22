"""
crash_logging.py — shared logging / crash-dump infrastructure for R9Tools.

Purpose
-------
The shipped app currently has zero visible diagnostics: everything is bare
``print()`` calls, and the packaged .exe is built with ``console=False``
(see R9Tools.spec), so none of that output is ever seen by a real user. If
the process dies — whether from an unhandled exception on the main thread,
an unhandled exception on a background daemon thread (which today just
silently kills that one thread with no visible trace), or a hard native
crash from ctypes/COM interop in the DX11 overlay — there is currently no
record of what happened.

This module is purely additive: it does not change any existing runtime
behavior. It only sets up:

  1. A rotating log file under a per-user, reliably-writable directory
     (``%LOCALAPPDATA%\\R9Tools\\logs``), used both when running from
     source and when running as a frozen PyInstaller .exe.
  2. ``faulthandler`` enabled against that log file, so a hard native
     crash (access violation / segfault-class failure) dumps the Python
     call stack at the moment of the crash — this is the only mechanism
     that can capture *native* crashes; a plain Python exception logger
     cannot.
  3. A ``sys.excepthook`` override that logs any unhandled main-thread
     exception (full traceback) before calling the previous hook (so
     PyInstaller's existing windowed-traceback dialog behavior, or the
     default stderr behavior when running from source, is preserved).
  4. A ``threading.excepthook`` override that logs any unhandled
     background-thread exception (full traceback, plus which thread)
     before calling the previous hook.

Call ``setup_logging()`` once, as early as possible in ``main()``. It is
safe to call more than once (idempotent) and is designed to never raise —
if the log directory can't be created/written for some reason, it silently
falls back to a null configuration rather than taking down the app it's
meant to protect.

KNOWN-BENIGN NOISE — read before treating a logged AV as a real crash
----------------------------------------------------------------------
Every single session start (confirmed across v1.0.0–v1.1.4, 36/36 session
starts in a ~20h live-testing window) logs exactly 3
"Windows fatal exception: access violation" blocks from the DX11Overlay
thread, all inside ``dx11_overlay.py``'s ``_create_window`` — twice at the
``RegisterClassW`` call and once at ``CreateWindowExW`` — then the overlay
proceeds and runs correctly for the rest of the session (crosshair,
click-through, indicators all verified working live every time this was
tested).

This is a `faulthandler` false positive, not a real crash, confirmed by:
  - Windows Event Viewer's Application log has ZERO "Application Error" /
    "Windows Error Reporting" entries for R9Tools.exe or python.exe at any
    of these 36 session-start timestamps/PIDs (checked both the exact time
    window and the last 200 Application-log crash/hang events overall —
    none belong to this app, ever).
  - The log never shows "[DX11Overlay] Fatal: ..." (the message logged by
    `_run`'s except-block) at these points — meaning `_create_window()`
    ran to completion and returned normally every time; the "exception"
    never actually propagated as a real Python-visible fault.
  - On Windows, `faulthandler` installs its hook via a vectored exception
    handler, which observes *every* first-chance exception in the process
    — including ones fully caught and resolved internally further down the
    call stack (e.g. by the GPU driver, DWM, or third-party software that
    hooks window-creation APIs like `RegisterClassW`/`CreateWindowExW` via
    guard-page/INT3-style instrumentation — a common technique for overlay
    injectors and some security/EDR tools) — before returning
    EXCEPTION_CONTINUE_SEARCH and letting execution resume normally. It is
    not a last-chance/unhandled-exception filter, so seeing a dump here
    does not by itself mean the process was ever in danger.

Do NOT "fix" this by disabling faulthandler or by suppressing all AV
reports — that would blind us to real future native crashes. If it needs
addressing at all, the narrow fix is investigating why RegisterClassW/
CreateWindowExW specifically trip a first-chance AV on this machine (likely
third-party window-hooking software, not our code) — not weakening crash
detection generally. Left alone as documented noise as of 2026-08-22.
"""
import faulthandler
import logging
import logging.handlers
import os
import sys
import threading

_LOG_FILE_HANDLE = None   # kept open for the lifetime of the process (faulthandler needs it)
_SETUP_DONE = False
_LOCK = threading.Lock()

_LOG_DIR_NAME = "R9Tools"
_LOG_SUBDIR = "logs"
_LOG_FILE_NAME = "r9tools.log"

_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per file
_BACKUP_COUNT = 3              # keep a few rotated copies


def _default_log_dir() -> str:
    """Pick a per-user, reliably-writable log directory.

    Prefer %LOCALAPPDATA%\\R9Tools\\logs — this is robust against
    install-directory permission quirks even though the app itself runs
    elevated (admin-owned Program Files-style directories can still have
    surprising ACLs, and per-user AppData is always writable by the user
    running the process). Falls back to a temp directory, then finally to
    the current working directory, if LOCALAPPDATA isn't set for some
    reason.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    return os.path.join(base, _LOG_DIR_NAME, _LOG_SUBDIR)


def get_log_path() -> str:
    """Return the full path to the active log file (does not create it)."""
    return os.path.join(_default_log_dir(), _LOG_FILE_NAME)


def _install_excepthook():
    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            logging.getLogger("r9tools.crash").critical(
                "Unhandled exception on main thread",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass   # never let the logger itself break crash reporting
        # Always defer to the previous hook (PyInstaller's windowed-traceback
        # dialog, or the default stderr printer when running from source).
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def _install_threading_excepthook():
    previous_hook = threading.excepthook

    def _hook(args):
        try:
            thread_name = args.thread.name if args.thread is not None else "<unknown>"
            logging.getLogger("r9tools.crash").critical(
                "Unhandled exception on background thread %r",
                thread_name,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass
        try:
            previous_hook(args)
        except Exception:
            pass

    threading.excepthook = _hook


def setup_logging() -> str:
    """Configure rotating-file logging, faulthandler, and exception hooks.

    Safe to call multiple times — only the first call takes effect.
    Never raises: any failure (unwritable directory, etc.) is swallowed
    and logging degrades to a no-op rather than crashing the app.

    Returns the log file path that was configured (best-effort; may be
    empty string if setup failed entirely).
    """
    global _LOG_FILE_HANDLE, _SETUP_DONE

    with _LOCK:
        if _SETUP_DONE:
            return get_log_path()

        log_path = ""
        try:
            log_dir = _default_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, _LOG_FILE_NAME)

            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)

            handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            root_logger.addHandler(handler)

            # faulthandler needs its own live file handle (it writes at the C
            # level during a fatal signal, it can't go through the logging
            # module). Open a separate append-mode handle to the same file
            # and keep it referenced for the process lifetime.
            _LOG_FILE_HANDLE = open(log_path, "a", encoding="utf-8", buffering=1)
            faulthandler.enable(file=_LOG_FILE_HANDLE, all_threads=True)

            _install_excepthook()
            _install_threading_excepthook()

            try:
                from version import APP_VERSION
            except Exception:
                APP_VERSION = "unknown"

            root_logger.info(
                "===== R9Tools session start (version=%s, frozen=%s, pid=%s) =====",
                APP_VERSION, getattr(sys, "frozen", False), os.getpid(),
            )
        except Exception:
            # Logging setup must never take down the app it's protecting.
            # If anything above failed, leave logging as a best-effort no-op.
            try:
                logging.getLogger().addHandler(logging.NullHandler())
            except Exception:
                pass

        _SETUP_DONE = True
        return log_path
