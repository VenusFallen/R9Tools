"""
R9Tools - Gaming Accessibility Toolkit
Run as administrator (required for Interception driver).
"""
import sys
import subprocess

from PySide6.QtCore import qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication

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
from recoil       import RecoilEngine
from macro_engine import MacroEngine
from bridge       import UIBridge
from dx11_overlay import DX11Overlay
from stats_poller import StatsPoller
from panel_window import PanelWindow


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
    _interception_driver(start=True)
    qInstallMessageHandler(_qt_message_filter)
    app = QApplication(sys.argv)

    profileData = prof.load()
    cfg = prof.activeSettings(profileData)
    cfg["recoil"]["enabled"]    = False   # always start disabled
    cfg["crosshair"]["enabled"] = False
    cfg["remapper"]["enabled"]  = False
    cfg.setdefault("stats", {})["enabled"] = False

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

    # panel_win starts hidden; overlay hotkey shows/hides it
    app.exec()

    engine.stop()
    macro_engine.stop()
    stats_poller.stop()
    dx_overlay.stop()
    _interception_driver(start=False)


if __name__ == "__main__":
    main()
