"""
R9Tools - Gaming Accessibility Toolkit
Run as administrator (required for Interception driver).
"""
import logging
import sys
import subprocess
import threading

from PySide6.QtCore import qInstallMessageHandler, QtMsgType, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from crash_logging import setup_logging

# Interception kernel filter driver service names.
#
# CONFIRMED (2026-08-22, urgent investigation into "completely non-functional
# after fresh install"): these were previously "keyboard_filter"/"mouse_filter",
# which do NOT exist under any circumstance -- installer_assets\
# install-interception.exe (the actual oblitum/Interception command-line
# installer bundled by R9Tools.iss) registers the driver's class upper-filter
# services as plain "keyboard" and "mouse" (DisplayName "Keyboard/Mouse Upper
# Filter Driver"), verified empirically via `sc.exe query` and a direct
# registry dump of HKLM\SYSTEM\CurrentControlSet\Services\keyboard and
# \mouse after running install-interception.exe /install by hand. The old
# names meant `sc start`/`sc stop` here always failed with error 1060
# (service does not exist) -- silently, since the return code was never
# checked (see below) -- on every single run, on every machine, forever.
# That said, this name fix alone does NOT make a *freshly installed* driver
# usable this session: Interception is a legacy class filter driver loaded
# by the PnP manager only when the Keyboard/Mouse device stacks next
# enumerate, i.e. only after a reboot -- install-interception.exe itself
# prints "Interception successfully installed. You must reboot for it to
# take effect." every time it runs. See R9Tools.iss's NeedRestart()
# handling for the installer-side half of this fix.
#
# DESIGN (2026-08-22, quit-time system-wide input freeze investigation):
# these driver SERVICES are now meant to stay loaded/running permanently
# once installed, for the lifetime of the machine session -- R9Tools no
# longer stops them on quit (see the removed stop-on-quit call, below in
# main()). Interception is a no-op passthrough at the driver level when no
# process has an open, filtered device handle against it: leaving the
# service running while R9Tools isn't even open has no behavioral effect on
# the rest of the system. What actually determines whether system input is
# captured is RecoilEngine's own open device handles (see recoil.py,
# RecoilEngine.stop()/_destroyInterception()) -- closing those is a fast,
# in-process operation, not a slow PnP-level driver detach, and is what
# fixed the ~1s system-wide input freeze previously seen on every quit.
_INTERCEPTION_SERVICES = ["keyboard", "mouse"]


# ERROR_SERVICE_ALREADY_RUNNING. Interception's driver services are class
# upper filters that the PnP manager loads automatically as part of the
# Keyboard/Mouse device stack at boot (once the registry entries have
# survived a reboot -- see the big comment above). That means a completely
# healthy, already-working session will have this service already running
# before the app ever calls `sc start`, and `sc start` on an
# already-running service legitimately fails with this code -- it must NOT
# be logged as a critical failure, or every normal session would log a
# false alarm. This has not been empirically exercised post-reboot on this
# service (see the driver-name comment above for what *has* been verified);
# it's included defensively based on documented `sc`/Win32 service-control
# semantics, not observed behavior.
_ERROR_SERVICE_ALREADY_RUNNING = 1056


def _interception_driver(start: bool) -> None:
    """Start or stop the Interception kernel filter driver services.
    Requires administrator privileges (enforced by the OS).

    Failures are logged critically rather than silently swallowed -- this
    was previously a bare subprocess.run() with no return-code check at
    all, which made a missing/not-yet-loaded driver (e.g. right after a
    fresh install, before the required reboot) completely invisible from
    the app's own log, forcing manual `sc query` investigation to even
    notice the driver wasn't there.

    Only ever called with start=True now (once, at launch, as a fast,
    idempotent safety net in case the driver isn't already running for
    some reason -- ERROR_SERVICE_ALREADY_RUNNING makes a repeat `sc start`
    on an already-running service cheap and harmless). The driver services
    are deliberately never stopped from here anymore -- see the DESIGN
    comment above _INTERCEPTION_SERVICES for why -- but the start=False
    path is kept rather than deleted, since stopping the driver services
    is still a legitimate thing for a future explicit uninstall/cleanup
    flow to need."""
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

# Delay (ms) after the Qt event loop starts before the automatic
# update-check runs, so it never competes with driver init / engine start /
# overlay start for startup time — those all happen synchronously above,
# before app.exec() is ever called.
_AUTO_UPDATE_CHECK_DELAY_MS = 2000

# Holds the live "Downloading update..." QMessageBox (if any) so it isn't
# garbage-collected out from under itself: it's a parentless top-level
# widget referenced only via a bound-method signal connection once
# _onUpdateAvailable() returns, and PySide6/Qt silently disconnects (rather
# than erroring) when a connected receiver is garbage-collected — so without
# this, the dialog would be collected and its progress updates would
# silently stop landing anywhere. Only one can ever be active at a time
# (the automatic check only runs once per launch), so a single module-level
# slot is sufficient.
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
    _interception_driver(start=True)
    qInstallMessageHandler(_qt_message_filter)
    app = QApplication(sys.argv)
    # This app has no "main window" in the usual sense — panel_win/_TopBarWindow
    # start hidden (see below) and only appear via the overlay hotkey, so any
    # transient dialog (e.g. the auto-update-available QMessageBox below) can
    # briefly be the ONLY visible top-level widget. Qt's default
    # quitOnLastWindowClosed=True treats that dialog closing — via EITHER
    # button, or even the titlebar X — as "the last window closed" and quits
    # the whole app (and, since this runs under `if __name__ == "__main__"`,
    # the whole process) right then. That's a real bug that was previously
    # observed: clicking "Later" on the update dialog closed the entire app,
    # and clicking "Update" raced the same auto-quit against the background
    # download/install thread, so the install step's queued signal
    # (_sigDoInstall, see panels/settings.py) sometimes never got delivered
    # because the main event loop had already torn down. All real quit paths
    # in this app are explicit (bridge.quitRequested -> app.quit(), the
    # topbar's quit button, and updater's post-install app.quit()), so
    # disabling this automatic behavior is safe and doesn't remove any way
    # to actually quit.
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

    # Engine → bridge (interception thread calls these directly)
    engine.setOverlayCallback(bridge.overlayToggled.emit)
    engine.setToggleCallback(bridge.recoilToggled.emit)
    engine.setStrengthCallback(bridge.strengthChanged.emit)
    engine.setQuitCallback(bridge.quitRequested.emit)
    # No hotkeys/remaps/macros/mouse-forwarding work at all if this fires —
    # see RecoilEngine._bringUpInterception(). Already logged critically from
    # the listen thread itself; this is just an additional, guaranteed-to-run
    # main-thread breadcrumb in case logging setup itself is degraded.
    # TODO(ui-agent): consider a dedicated bridge Signal + DX11 overlay
    # "input engine failed" indicator here rather than only a log line —
    # left as a follow-up, this callback hook is the wiring point for it.
    engine.setInputFailedCallback(
        lambda: logging.critical(
            "R9Tools input engine failed to start — the app is running but "
            "no hotkeys, remaps, macros, or recoil compensation will work "
            "this session. See earlier log entries for the Interception "
            "driver bring-up failure."
        )
    )

    # StatsPoller → bridge (poller thread → main thread via QueuedConnection)
    stats_poller.setCallback(bridge.statsUpdated.emit)

    engine.start()
    dx_overlay.start()
    stats_poller.start()

    # A few seconds after the event loop is up and running (well after
    # driver init / engine start / overlay start above, so it never
    # competes with those for startup time) — check for a newer release in
    # the background. This is purely additive on top of the existing manual
    # "Check for Updates" button in Settings; it doesn't touch that flow.
    QTimer.singleShot(_AUTO_UPDATE_CHECK_DELAY_MS, lambda: _startAutoUpdateCheck(cfg, bridge))

    # panel_win starts hidden; overlay hotkey shows/hides it
    app.exec()

    # engine.stop() now destroys RecoilEngine's own Interception context
    # (closing its open, filtered keyboard/mouse device handles) as the
    # FIRST thing it does, before joining any of its threads — see
    # RecoilEngine.stop()/_destroyInterception() in recoil.py. That's what
    # actually releases system-wide input, not the driver SERVICE being
    # stopped: the driver deliberately stays loaded/running across app
    # sessions now (see _INTERCEPTION_SERVICES comment above), and closing
    # this process's device handles is what determines whether the driver
    # has anything to capture. There is deliberately no
    # `_interception_driver(start=False)` call here anymore — stopping the
    # driver service on quit was the slow, ~1s, PnP-level operation
    # previously responsible for the system-wide keyboard/mouse freeze
    # testers reported when closing the app; it is no longer part of the
    # quit path at all.
    engine.stop()

    macro_engine.stop()
    stats_poller.stop()
    dx_overlay.stop()


if __name__ == "__main__":
    main()
