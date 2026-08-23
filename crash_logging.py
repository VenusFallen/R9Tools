"""
crash_logging.py — shared logging / crash-dump infrastructure for R9Tools.

The shipped app has no visible diagnostics otherwise: everything is bare
print() calls, and the packaged .exe runs with console=False, so nothing is
seen by a real user and a process death (unhandled exception, native crash)
leaves no record. This module is purely additive — it changes no existing
runtime behavior — and sets up:

  1. A rotating log file under a per-user, reliably-writable directory
     (``%LOCALAPPDATA%\\R9Tools\\logs``), used both from source and frozen.
  2. ``faulthandler`` enabled against that log file, so a hard native
     crash (access violation / segfault-class failure) dumps the Python
     call stack — the only mechanism that can capture native crashes.
  3. A ``sys.excepthook`` override that logs any unhandled main-thread
     exception before calling the previous hook (preserving PyInstaller's
     windowed-traceback dialog / default stderr behavior).
  4. A ``threading.excepthook`` override that logs any unhandled
     background-thread exception (plus which thread) before calling the
     previous hook.

Call ``setup_logging()`` once, as early as possible in ``main()``.
Idempotent and never raises — falls back to a null configuration if the
log directory can't be created/written.

KNOWN-BENIGN NOISE — read before treating a logged AV as a real crash
----------------------------------------------------------------------
Every session start logs 3 "Windows fatal exception: access violation"
entries from DX11Overlay's ``_create_window`` (``RegisterClassW`` x2,
``CreateWindowExW`` x1); the overlay then proceeds and runs normally. This
is a `faulthandler` false positive — Windows' vectored exception handler
(which faulthandler hooks into) sees every first-chance exception,
including ones fully caught and resolved internally (e.g. by the GPU
driver, DWM, or third-party window-hooking software), not just genuinely
unhandled ones.

Do NOT "fix" this by disabling faulthandler or suppressing all AV
reports — that would blind us to real future native crashes.
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

    Prefers %LOCALAPPDATA%\\R9Tools\\logs — robust against install-directory
    permission quirks even though the app itself runs elevated. Falls back
    to a temp directory, then the current working directory, if
    LOCALAPPDATA isn't set.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    return os.path.join(base, _LOG_DIR_NAME, _LOG_SUBDIR)


def get_log_path() -> str:
    """Return the full path to the active log file (does not create it)."""
    return os.path.join(_default_log_dir(), _LOG_FILE_NAME)


def get_log_dir() -> str:
    """Return the log directory (``%LOCALAPPDATA%\\R9Tools\\logs`` or its
    fallback — see _default_log_dir()) without creating it. Public wrapper
    around _default_log_dir() so other modules (e.g. updater.py, to place
    an Inno Setup /LOG= install log alongside the app's own log) have one
    canonical place to ask "where do R9Tools logs live" instead of
    duplicating the %LOCALAPPDATA% fallback chain."""
    return _default_log_dir()


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
