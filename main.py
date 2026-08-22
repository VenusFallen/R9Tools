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

# Interception kernel filter driver service names
_INTERCEPTION_SERVICES = ["keyboard_filter", "mouse_filter"]


def _interception_driver(start: bool) -> None:
    """Start or stop the Interception kernel filter driver services.
    Requires administrator privileges (enforced by the OS)."""
    action = "start" if start else "stop"
    for svc in _INTERCEPTION_SERVICES:
        subprocess.run(
            ["sc", action, svc],
            capture_output=True,   # suppress console output
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
_AUTO_UPDATE_CHECK_DELAY_MS = 5000

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

    # engine.stop() joins RecoilEngine's own threads, including the
    # _listenLoop thread that is the ONLY thing forwarding system-wide
    # keyboard/mouse input through to the OS while the Interception kernel
    # filter driver is active (it filters ALL input system-wide, not just
    # input meant for R9Tools — see _interception_driver above). Once that
    # thread is dead, the driver is still capturing every keystroke/mouse
    # move with nothing left to forward it anywhere, so the whole system's
    # input appears to lock up until the driver services are actually
    # stopped below. Release the driver immediately after engine.stop()
    # returns, rather than waiting on macro_engine/stats_poller/dx_overlay
    # teardown first — none of those subsystems touch the interception
    # driver, so there's no reason the system-wide input lockup window
    # should include their (unrelated, sometimes multi-hundred-ms) shutdown
    # time. Measured impact: stats_poller.stop() alone can block for up to
    # a full poll interval (~0.2-2s depending on the configured stats
    # update rate) because its poll loop sleeps in one uninterruptible
    # `time.sleep()` call per cycle — see stats_poller.py.
    engine.stop()
    _interception_driver(start=False)

    macro_engine.stop()
    stats_poller.stop()
    dx_overlay.stop()


if __name__ == "__main__":
    main()
