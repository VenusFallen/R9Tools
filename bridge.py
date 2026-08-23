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
    statsUpdated    = Signal(dict)  # new hardware stats from StatsPoller
    quitRequested   = Signal()      # quit hotkey pressed
    updateAvailable = Signal(str)   # background auto-update check found a newer release (arg: latest version str)
    inputEngineFailed = Signal()    # input engine (Interception) failed — startup or mid-session, fires once

    # WM_DEVICECHANGE-based detection (see device_watch.py) — a second,
    # faster path that knows exactly which device(s) failed and can
    # fire/clear more than once per session, unlike inputEngineFailed above
    # (no-arg, fires once). See input_failed_notice.py for how the two are
    # unified in the UI.
    deviceInputFailed        = Signal(list)  # settled batch of failed device instance IDs
    deviceInputRecovered     = Signal(list)  # remaining failed device instance IDs (empty = fully recovered)
    reconnectAttemptFinished = Signal()      # background Disable/Enable-PnpDevice attempt completed (no success/failure claim)
