"""
Thread-safe signal bridge from RecoilEngine callbacks to the Qt UI.

The engine's interception thread calls `.emit()` methods directly.
PySide6 automatically delivers cross-thread signal emissions via
QueuedConnection — no invokeMethod, no locks, no @Slot required
on the receiving end.
"""
from PySide6.QtCore import QObject, Signal


class UIBridge(QObject):
    overlayToggled  = Signal()      # overlay show/hide hotkey pressed
    recoilToggled   = Signal(bool)  # recoil enabled state changed
    strengthChanged = Signal(int)   # recoil strength value changed
    quitRequested   = Signal()      # quit hotkey pressed
