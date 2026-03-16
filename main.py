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
from recoil        import RecoilEngine
from macro_engine  import MacroEngine
from bridge        import UIBridge
from overlay_window import OverlayWindow
from panel_window  import PanelWindow


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

    engine       = RecoilEngine(cfg)
    macro_engine = MacroEngine(cfg)
    engine.setMacroEngine(macro_engine)

    # Overlay needs cfg + engine before onSettingsChanged is defined
    overlay_win = OverlayWindow(cfg, engine)

    def onSettingsChanged(updated: dict):
        engine.updateSettings(updated)
        macro_engine.updateSettings(updated)
        overlay_win.refresh()  # recompute mask + repaint when settings change

    # Signal bridge — lives on the main thread; engine stores .emit references.
    # Cross-thread emissions are automatically delivered via QueuedConnection.
    bridge = UIBridge()

    panel_win = PanelWindow(cfg, profileData, engine, macro_engine, onSettingsChanged)

    # Bridge → UI
    bridge.overlayToggled.connect(panel_win.toggleOverlay)
    bridge.recoilToggled.connect(panel_win.onRecoilToggled)
    bridge.recoilToggled.connect(lambda _: overlay_win.refresh())
    bridge.strengthChanged.connect(panel_win.onStrengthChanged)
    bridge.strengthChanged.connect(overlay_win.showStrengthIndicator)
    bridge.quitRequested.connect(app.quit)

    # Engine → bridge (interception thread calls these directly)
    engine.setOverlayCallback(bridge.overlayToggled.emit)
    engine.setToggleCallback(bridge.recoilToggled.emit)
    engine.setStrengthCallback(bridge.strengthChanged.emit)
    engine.setQuitCallback(bridge.quitRequested.emit)

    engine.start()

    # overlay_win starts hidden; _refreshMask() shows it on demand and applies WS_EX_TRANSPARENT via showEvent
    # panel_win starts hidden; overlay hotkey shows/hides it

    app.exec()

    engine.stop()
    macro_engine.stop()
    _interception_driver(start=False)


if __name__ == "__main__":
    main()
