"""
R9Tools - Gaming Accessibility Toolkit
Run as administrator (required for Interception driver).
"""
import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
import subprocess
import threading

from PySide6.QtCore import qInstallMessageHandler, QtMsgType, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from crash_logging import setup_logging

# Named Win32 mutex this process creates and holds for its entire lifetime,
# purely so R9Tools.iss's `AppMutex=R9Tools_AppMutex` (paired with
# `CloseApplications=yes`) has something concrete to detect via Windows'
# Restart Manager during a silent self-update install. Must match the
# AppMutex name in R9Tools.iss's [Setup] section exactly (case-sensitive).
_APP_MUTEX_NAME = "R9Tools_AppMutex"

# Kept alive for the process lifetime; deliberately never CloseHandle()'d
# (see _create_app_mutex()'s docstring for why).
_app_mutex_handle = None


def _create_app_mutex() -> None:
    """Create (and hold, never release) a named Win32 mutex identifying
    this running R9Tools process to Windows' Restart Manager.

    This is NOT a single-instance lock: CreateMutexW is called with
    bInitialOwner=False and the return value/GetLastError() is never
    checked for ERROR_ALREADY_EXISTS, so multiple R9Tools processes can
    still run side-by-side exactly as before this change.

    Its only purpose is to close the race in updater.py's
    launch_installer_and_quit(): previously, the extracted installer was
    launched as a detached process and this app called
    QApplication.quit() right after, hoping this process's file lock on
    R9Tools.exe released before the installer's file-copy step ran — a
    bare timing race with no synchronization. With R9Tools.iss's
    `CloseApplications=yes` + `AppMutex=R9Tools_AppMutex` now set, Setup
    uses Restart Manager to find the process holding this mutex and
    actually wait for/force-close it before touching any files, instead
    of racing a fire-and-forget Popen against this process's own shutdown
    teardown (engine.stop() closing device handles, threads joining,
    etc.).

    The HANDLE is stashed in a module-level global only so it can never
    accidentally be a candidate for cleanup; it does not need explicit
    closing — Windows closes it automatically when this process exits,
    which is exactly the condition Restart Manager is waiting to detect.
    """
    global _app_mutex_handle
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _app_mutex_handle = kernel32.CreateMutexW(None, False, _APP_MUTEX_NAME)
        if not _app_mutex_handle:
            logging.warning(
                "CreateMutexW(%r) failed (error %d) -- a silent self-update "
                "install will fall back to relying on this process having "
                "already exited on its own by the time Setup's file-copy "
                "step runs, instead of Restart Manager waiting for/closing "
                "it deterministically.",
                _APP_MUTEX_NAME, ctypes.GetLastError(),
            )
    except Exception:
        logging.exception("Failed to create app mutex %r", _APP_MUTEX_NAME)

# Interception kernel filter driver service names. install-interception.exe
# (bundled by R9Tools.iss) registers these as "keyboard"/"mouse" (DisplayName
# "Keyboard/Mouse Upper Filter Driver"), not "keyboard_filter"/"mouse_filter".
# Interception is a legacy class upper-filter driver: after a fresh install
# it only attaches to the Keyboard/Mouse device stacks on the next reboot —
# see R9Tools.iss's NeedRestart() for the installer-side half of this.
#
# These services are meant to stay loaded/running permanently once
# installed — R9Tools no longer stops them on quit. Interception is a no-op
# passthrough with no open filtered device handle attached, so what
# actually determines whether system input is captured is RecoilEngine's
# own open device handles (see recoil.py's stop()/_destroyInterception()),
# not the service's running state.
_INTERCEPTION_SERVICES = ["keyboard", "mouse"]


# ERROR_SERVICE_ALREADY_RUNNING. Interception's driver services are class
# upper filters the PnP manager loads automatically at boot, so a healthy
# session will already have them running before `sc start` is ever called
# — this must not be logged as a critical failure, or every normal session
# would log a false alarm.
_ERROR_SERVICE_ALREADY_RUNNING = 1056


def _interception_driver(start: bool) -> None:
    """Start or stop the Interception kernel filter driver services.
    Requires administrator privileges (enforced by the OS).

    Failures are logged critically rather than silently swallowed, so a
    missing/not-yet-loaded driver (e.g. right after a fresh install, before
    the required reboot) is visible in the app's own log.

    Only ever called with start=True now, once at launch as an idempotent
    safety net — the driver services are deliberately never stopped from
    here (see _INTERCEPTION_SERVICES above), but start=False is kept for a
    possible future uninstall/cleanup flow."""
    action = "start" if start else "stop"
    for svc in _INTERCEPTION_SERVICES:
        result = subprocess.run(
            ["sc", action, svc],
            capture_output=True,   # suppress console output
            text=True,
        )
        if (
            start
            and result.returncode != 0
            and result.returncode != _ERROR_SERVICE_ALREADY_RUNNING
        ):
            logging.critical(
                "Interception driver service %r failed to %s (sc.exe exit "
                "code %d): %s -- if this service also fails a manual "
                "`sc query %s`, the Interception driver isn't loaded yet. "
                "This is expected immediately after a fresh install/"
                "reinstall (a reboot is required before Windows loads a "
                "newly registered class filter driver); if it persists "
                "after a reboot, the driver install itself likely failed.",
                svc, action, result.returncode,
                (result.stderr or result.stdout or "<no output>").strip(),
                svc,
            )

import profiles as prof
import updater
from version      import APP_VERSION
from recoil       import RecoilEngine
from macro_engine import MacroEngine
from bridge       import UIBridge
from dx11_overlay import DX11Overlay
from stats_poller import StatsPoller
from panel_window import PanelWindow
from input_failed_notice import InputFailedNotice

# Delay (ms) after the Qt event loop starts before the automatic
# update-check runs, so it never competes with driver init / engine start /
# overlay start for startup time — those all happen synchronously above,
# before app.exec() is ever called.
_AUTO_UPDATE_CHECK_DELAY_MS = 2000

# Holds the live "Downloading update..." QMessageBox so it isn't
# garbage-collected out from under its signal connection (PySide6 silently
# disconnects a GC'd receiver instead of erroring). Only one can ever be
# active (the automatic check runs once per launch), so a module-level slot
# is enough.
_auto_update_progress_dialog = None


def _startAutoUpdateCheck(cfg: dict, bridge: UIBridge) -> None:
    """Kick off a background-thread check for a newer R9Tools release, if
    the user hasn't disabled it. Never touches Qt/UI directly from the
    background thread — the result is handed back via a bridge Signal,
    which PySide6 auto-queues onto the main thread (same pattern as every
    other cross-thread callback in this app; see bridge.py)."""
    if not cfg.get("auto_update_check", {}).get("enabled", True):
        return

    def worker():
        try:
            avail, latest = updater.check_app_update(APP_VERSION)
        except Exception:
            logging.exception("Automatic R9Tools update check failed")
            return
        if avail:
            bridge.updateAvailable.emit(latest)

    threading.Thread(target=worker, daemon=True).start()


def _onUpdateAvailable(latest: str, panel_win: PanelWindow) -> None:
    """Shown when the automatic startup check (see _startAutoUpdateCheck)
    finds a newer release. This is purely additive on top of the existing
    manual "Check for Updates" flow in Settings — it does not touch it."""
    box = QMessageBox()
    box.setWindowTitle("R9Tools Update")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(f"R9Tools v{latest} is available. Update now?")
    updateBtn = box.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(updateBtn)
    box.exec()

    if box.clickedButton() is not updateBtn:
        return  # "Later" — dismiss and continue; no re-prompt this session

    # Minimal visible feedback for the ~250MB download — non-modal so it
    # doesn't block the queued status-signal updates from reaching it.
    # Reuses the exact same download/install/quit flow the Settings-tab
    # Update button uses (see PanelWindow.triggerAutoUpdate ->
    # SettingsPanel.triggerAutoUpdate); nothing is duplicated here.
    global _auto_update_progress_dialog
    progress = QMessageBox()
    progress.setWindowTitle("R9Tools Update")
    progress.setIcon(QMessageBox.Icon.Information)
    progress.setText("Downloading update...")
    progress.setStandardButtons(QMessageBox.StandardButton.Close)
    progress.setModal(False)
    progress.show()
    _auto_update_progress_dialog = progress  # keep alive — see comment above

    panel_win.triggerAutoUpdate(latest, on_status=progress.setText)


def _qt_message_filter(msg_type, _context, msg):
    if "Unable to set geometry" in msg:
        return
    if msg_type == QtMsgType.QtWarningMsg:
        print(f"Qt warning: {msg}", file=sys.stderr)
    elif msg_type == QtMsgType.QtCriticalMsg:
        print(f"Qt critical: {msg}", file=sys.stderr)
    elif msg_type == QtMsgType.QtFatalMsg:
        print(f"Qt fatal: {msg}", file=sys.stderr)


def main():
    setup_logging()
    _create_app_mutex()
    _interception_driver(start=True)
    qInstallMessageHandler(_qt_message_filter)
    app = QApplication(sys.argv)
    # panel_win starts hidden, so a transient dialog (e.g. the update
    # prompt) can briefly be the only visible top-level widget. Without
    # this, Qt's default quitOnLastWindowClosed treats that dialog closing
    # as "last window closed" and kills the whole app; all real quit paths
    # here are explicit (bridge.quitRequested, topbar quit, post-install
    # quit) so disabling it is safe.
    app.setQuitOnLastWindowClosed(False)

    profileData = prof.load()
    cfg = prof.activeSettings(profileData)
    cfg["recoil"]["enabled"]    = False   # always start disabled
    cfg["crosshair"]["enabled"] = False
    cfg["remapper"]["enabled"]  = False
    cfg.setdefault("stats", {})["enabled"] = False
    # running_indicator is intentionally left alone (not forced False) — it's
    # a simple "R9Tools is loaded" badge, not a gameplay/visual module, and
    # forcing it off on every launch would defeat its own purpose.
    cfg.setdefault("running_indicator", {"enabled": True})
    # auto_update_check is also intentionally left alone (not forced False) —
    # it's a persistent app-behavior preference, not a per-session overlay
    # feature; see profiles.py and _startAutoUpdateCheck() below.
    cfg.setdefault("auto_update_check", {"enabled": True})

    engine       = RecoilEngine(cfg)
    macro_engine = MacroEngine(cfg)
    engine.setMacroEngine(macro_engine)

    dx_overlay  = DX11Overlay(cfg, engine)
    stats_poller = StatsPoller(cfg)

    def onSettingsChanged(updated: dict):
        engine.updateSettings(updated)
        macro_engine.updateSettings(updated)
        stats_poller.updateSettings(updated)
        dx_overlay.refresh()

    # Signal bridge — lives on the main thread; engine stores .emit references.
    # Cross-thread emissions are automatically delivered via QueuedConnection.
    bridge = UIBridge()

    panel_win = PanelWindow(cfg, profileData, engine, macro_engine, onSettingsChanged)

    # Independent top-level window (not nested in panel_win's widget tree —
    # panel_win starts hidden and this must be able to appear on its own,
    # see input_failed_notice.py). Kept alive via this local reference for
    # the lifetime of main() (i.e. until app.exec() returns).
    input_failed_notice = InputFailedNotice(bridge)

    # Combined "is input currently failed" state feeding dx_overlay's single
    # red-badge bool: "old" (recoil.py's I/O-exception counter) is sticky
    # for the session with no recovery, "devices" (device_watch.py's
    # WM_DEVICECHANGE detection) can grow/shrink as devices fail/recover.
    # Both mutations only ever happen on the Qt main thread — no lock needed.
    _input_fail_state = {"old": False, "devices": []}

    def _recomputeBadge():
        dx_overlay.set_input_failed(
            _input_fail_state["old"] or bool(_input_fail_state["devices"]))

    def _onOldPathFailed():
        _input_fail_state["old"] = True
        _recomputeBadge()

    def _onDeviceInputFailed(ids):
        _input_fail_state["devices"] = list(ids)
        _recomputeBadge()
        bridge.deviceInputFailed.emit(list(ids))

    def _onDeviceInputRecovered(ids):
        _input_fail_state["devices"] = list(ids)
        _recomputeBadge()
        bridge.deviceInputRecovered.emit(list(ids))

    # panel_win owns the HWND device-change notifications are registered
    # against, so these callbacks run directly on the Qt main thread
    # (nativeEventFilter) — still re-emitted as bridge Signals so
    # input_failed_notice.py stays decoupled from panel_window internals.
    panel_win.setDeviceFailureCallbacks(_onDeviceInputFailed, _onDeviceInputRecovered)

    # Bridge → UI
    bridge.overlayToggled.connect(panel_win.toggleOverlay)
    bridge.recoilToggled.connect(panel_win.onRecoilToggled)
    bridge.recoilToggled.connect(lambda _: dx_overlay.refresh())
    bridge.strengthChanged.connect(panel_win.onStrengthChanged)
    # dx_overlay methods are thread-safe (internal lock); DirectConnection is fine
    # because statsUpdated/strengthChanged are delivered on the Qt main thread
    # (StatsPoller → bridge uses QueuedConnection, engine callbacks → bridge are
    # emitted from the interception thread and Qt auto-queues them).
    bridge.strengthChanged.connect(dx_overlay.show_strength_indicator)
    bridge.statsUpdated.connect(dx_overlay.update_stats)
    bridge.quitRequested.connect(app.quit)
    bridge.updateAvailable.connect(lambda latest: _onUpdateAvailable(latest, panel_win))
    # inputEngineFailed (old path) is auto-queued onto the main thread by
    # PySide6 (emitted from the engine's listen thread below) — safe to
    # mutate _input_fail_state / touch dx_overlay / touch widgets here.
    bridge.inputEngineFailed.connect(_onOldPathFailed)
    bridge.inputEngineFailed.connect(input_failed_notice.trigger)
    # New path (device_watch.py) — see input_failed_notice.py for exactly
    # how "old" vs "device" mode is unified into one shared popup.
    bridge.deviceInputFailed.connect(input_failed_notice.triggerDevices)
    bridge.deviceInputRecovered.connect(input_failed_notice.recoverDevices)
    # Emitted from InputFailedNotice's background PnP reconnect thread —
    # this one genuinely needs the QueuedConnection, since it originates
    # off the Qt main thread.
    bridge.reconnectAttemptFinished.connect(input_failed_notice.reconnectFinished)

    # Engine → bridge (interception thread calls these directly)
    engine.setOverlayCallback(bridge.overlayToggled.emit)
    engine.setToggleCallback(bridge.recoilToggled.emit)
    engine.setStrengthCallback(bridge.strengthChanged.emit)
    engine.setQuitCallback(bridge.quitRequested.emit)
    # No hotkeys/remaps/macros/mouse-forwarding work at all if this fires
    # (see RecoilEngine._bringUpInterception()). Called from the engine's
    # background listen thread, so UI-facing work goes through the bridge
    # Signal (auto-queued onto the main thread); logging.critical() is kept
    # as a main-thread-independent breadcrumb in case logging is degraded.
    def _onInputEngineFailed():
        logging.critical(
            "R9Tools input engine failed to start — the app is running but "
            "no hotkeys, remaps, macros, or recoil compensation will work "
            "this session. See earlier log entries for the Interception "
            "driver bring-up failure."
        )
        bridge.inputEngineFailed.emit()

    engine.setInputFailedCallback(_onInputEngineFailed)

    # StatsPoller → bridge (poller thread → main thread via QueuedConnection)
    stats_poller.setCallback(bridge.statsUpdated.emit)

    engine.start()
    dx_overlay.start()
    stats_poller.start()

    # Delayed so it never competes with driver/engine/overlay init for
    # startup time; purely additive on top of the manual "Check for
    # Updates" button in Settings.
    QTimer.singleShot(_AUTO_UPDATE_CHECK_DELAY_MS, lambda: _startAutoUpdateCheck(cfg, bridge))

    # panel_win starts hidden; overlay hotkey shows/hides it
    app.exec()

    # engine.stop() closes RecoilEngine's own Interception device handles
    # first, before joining threads — that's what actually releases
    # system-wide input, not the driver service (which stays running
    # across sessions, see _INTERCEPTION_SERVICES above). There is
    # deliberately no _interception_driver(start=False) call here anymore;
    # stopping the service was the slow PnP operation that used to cause a
    # system-wide input freeze on quit.
    engine.stop()

    macro_engine.stop()
    stats_poller.stop()
    dx_overlay.stop()


if __name__ == "__main__":
    main()
