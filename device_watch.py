"""
device_watch.py — WM_DEVICECHANGE-based detection of a keyboard/mouse
device being disabled/disconnected mid-session.

Why this exists (confirmed empirically this session): when a keyboard/mouse
device is disabled via Device Manager / `Disable-PnpDevice` mid-session, the
Interception I/O calls (await_input/receive/send) don't throw — they just
silently stop returning input, indistinguishable from the user simply not
touching the device. recoil.py's existing consecutive-I/O-exception counter
(see RecoilEngine._onInterceptionIoFailure) cannot see this at all. Pushed
OS device-change notifications can — confirmed live via a working ctypes
RegisterDeviceNotificationW test (~1.0s detection vs ~1.8s for 0.5s-interval
`Get-PnpDevice` polling; ~23ms recovery detection after re-enable).

This module owns two independent, ctypes-only pieces:
  - Win32 plumbing to register for + parse WM_DEVICECHANGE device-interface
    notifications (register_device_notifications, parse_device_change,
    WM_DEVICECHANGE constant for the caller's native event filter to match
    on). No Qt dependency here — panel_window.py's _DeviceChangeFilter
    (a QAbstractNativeEventFilter, mirroring its existing
    _DisplayChangeFilter for WM_DISPLAYCHANGE) is what actually hooks this
    into the app's Qt message pump and owns the HWND.
  - DeviceFailureWatcher: a small, Qt-QTimer-driven (but injectable, see
    `schedule` param) debounce/batch state machine that turns noisy raw
    arrival/removal events into two clean, low-frequency callbacks:
    on_failed_batch(ids) and on_recovered(ids). Kept separate from the
    Win32 plumbing above so its logic is unit-testable without any ctypes
    memory reads or a live HWND — see tests/test_device_watch.py.

CRITICAL ctypes gotcha (confirmed today): ctypes defaults every unset
restype to 32-bit c_int. Pointer/handle-returning Win32 functions on this
64-bit process WILL overflow and crash unless .restype/.argtypes are set
explicitly on every function used here.
"""
import ctypes
import ctypes.wintypes as wintypes
import logging

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

WM_DEVICECHANGE = 0x0219

DBT_DEVICEARRIVAL        = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE   = 5
DEVICE_NOTIFY_WINDOW_HANDLE  = 0x00000000

# Well-known device interface GUIDs (ntddkbd.h / ntddmou.h / hidclass.h) —
# confirmed working via CLSIDFromString + RegisterDeviceNotificationW.
GUID_DEVINTERFACE_MOUSE    = "{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
GUID_DEVINTERFACE_KEYBOARD = "{884b96c3-56ef-11d1-bc8c-00a0c91405dd}"
GUID_DEVINTERFACE_HID      = "{4d1e55b2-f16f-11cf-88cb-001111000030}"

_ALL_GUIDS = (GUID_DEVINTERFACE_MOUSE, GUID_DEVINTERFACE_KEYBOARD, GUID_DEVINTERFACE_HID)

_user32 = ctypes.windll.user32
_ole32  = ctypes.windll.ole32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
    _fields_ = [
        ("dbcc_size",      wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved",  wintypes.DWORD),
        ("dbcc_classguid", _GUID),
        ("dbcc_name",      ctypes.c_wchar * 1),  # variable-length; name trails the struct in memory
    ]


_ole32.CLSIDFromString.restype  = ctypes.c_long  # HRESULT
_ole32.CLSIDFromString.argtypes = [wintypes.LPCOLESTR, ctypes.POINTER(_GUID)]

_user32.RegisterDeviceNotificationW.restype  = wintypes.HANDLE
_user32.RegisterDeviceNotificationW.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.DWORD]

_user32.UnregisterDeviceNotification.restype  = wintypes.BOOL
_user32.UnregisterDeviceNotification.argtypes = [wintypes.HANDLE]


def _make_guid(guid_str: str) -> _GUID:
    guid = _GUID()
    hr = _ole32.CLSIDFromString(guid_str, ctypes.byref(guid))
    if hr != 0:
        raise OSError(f"CLSIDFromString failed for {guid_str!r} (hr=0x{hr & 0xFFFFFFFF:08X})")
    return guid


def register_device_notifications(hwnd: int) -> list:
    """Register for arrival/removal notifications on the mouse, keyboard,
    and generic-HID device interface classes against the given real HWND
    (see panel_window.py's _TopBarWindow, which reuses its own winId() —
    the existing native-event-filter precedent already listens on this
    same window for WM_DISPLAYCHANGE).

    Returns the list of successfully-registered notification handles (for
    UnregisterDeviceNotification if a caller ever needs to tear this down;
    not currently called anywhere, matching this app's existing convention
    of not explicitly unwinding the WM_DISPLAYCHANGE filter either — both
    live for the app's lifetime and are cleaned up by process exit).
    Registration failures are logged and skipped rather than raised, so one
    bad GUID can't take down the other two."""
    handles = []
    for guid_str in _ALL_GUIDS:
        try:
            guid = _make_guid(guid_str)
            filt = _DEV_BROADCAST_DEVICEINTERFACE()
            filt.dbcc_size       = ctypes.sizeof(_DEV_BROADCAST_DEVICEINTERFACE)
            filt.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
            filt.dbcc_reserved   = 0
            filt.dbcc_classguid  = guid
            handle = _user32.RegisterDeviceNotificationW(
                wintypes.HWND(hwnd), ctypes.byref(filt), DEVICE_NOTIFY_WINDOW_HANDLE)
            if not handle:
                logging.warning(
                    "RegisterDeviceNotificationW failed for %s (GetLastError=%d) — "
                    "device-disable detection for that device class will be degraded "
                    "to the existing I/O-exception-counter path only.",
                    guid_str, ctypes.GetLastError())
                continue
            handles.append(handle)
        except OSError:
            logging.exception("Failed to register device-change notification for %s", guid_str)
    return handles


def _symlink_to_instance_id(symlink: str):
    """Parse a DEV_BROADCAST_DEVICEINTERFACE.dbcc_name symbolic-link path
    (e.g. r"\\\\?\\HID#VID_046D&PID_C24A&MI_00#8&d4d6dff&0&0000#{4d1e55b2-...}")
    into the device instance ID format `Get-PnpDevice`/`Disable-PnpDevice
    -InstanceId` expect (e.g. r"HID\\VID_046D&PID_C24A&MI_00\\8&D4D6DFF&0&0000").

    Strips the leading "\\\\?\\" prefix, takes the first 3 '#'-delimited
    segments (discarding the trailing device-interface-GUID segment),
    replaces '#' with '\\', and uppercases the result — verified against
    the exact example symlink captured during live testing this session.
    Returns None for anything that doesn't have the expected shape (fewer
    than 4 '#'-delimited segments) rather than guessing."""
    s = symlink
    if s.startswith("\\\\?\\"):
        s = s[4:]
    parts = s.split("#")
    if len(parts) < 4:
        return None
    return "\\".join(parts[:3]).upper()


def parse_device_change(wparam: int, lparam: int):
    """Given the wParam/lParam of a WM_DEVICECHANGE message, return
    ("arrival"|"removal", instance_id) if this is a device-interface
    arrival/removal event we can parse, else None (irrelevant wParam,
    non-device-interface broadcast type, or an unparsable/unexpected
    dbcc_name shape)."""
    if wparam == DBT_DEVICEARRIVAL:
        kind = "arrival"
    elif wparam == DBT_DEVICEREMOVECOMPLETE:
        kind = "removal"
    else:
        return None
    if not lparam:
        return None
    try:
        hdr = _DEV_BROADCAST_DEVICEINTERFACE.from_address(lparam)
        if hdr.dbcc_devicetype != DBT_DEVTYP_DEVICEINTERFACE:
            return None
        name_offset = _DEV_BROADCAST_DEVICEINTERFACE.dbcc_name.offset
        symlink = ctypes.wstring_at(lparam + name_offset)
    except (ValueError, TypeError, OSError):
        return None
    instance_id = _symlink_to_instance_id(symlink)
    if instance_id is None:
        return None
    return kind, instance_id


# ---------------------------------------------------------------------------
# Debounce / batch state machine
# ---------------------------------------------------------------------------

def _qt_schedule(ms: int, fn):
    """Default `schedule` implementation for DeviceFailureWatcher: fires
    `fn` once after `ms` milliseconds via a single-shot QTimer, and returns
    a zero-arg cancel callable. Only imports PySide6 lazily (at call time)
    so this module stays importable — and DeviceFailureWatcher's debounce
    logic stays unit-testable — without a live QApplication (see
    tests/test_device_watch.py, which injects a fake `schedule` instead).

    The fired/pending QTimer is kept alive via the module-level `_live` set
    below (not just via the returned closure) since an un-parented QTimer
    with no other Python references can otherwise be garbage-collected
    before it fires — a known PySide gotcha."""
    from PySide6.QtCore import QTimer

    timer = QTimer()
    timer.setSingleShot(True)

    def _onTimeout():
        _qt_schedule._live.discard(timer)
        fn()

    timer.timeout.connect(_onTimeout)
    _qt_schedule._live.add(timer)
    timer.start(ms)

    def _cancel():
        timer.stop()
        _qt_schedule._live.discard(timer)

    return _cancel


_qt_schedule._live = set()


class DeviceFailureWatcher:
    """Turns raw, noisy WM_DEVICECHANGE arrival/removal events (fed in via
    handle_removal()/handle_arrival()) into two clean callbacks:

      on_failed_batch(ids: list[str])
          Called once a removal has "settled" (no further arrival for that
          same device within `remove_settle_ms`) — i.e. treated as a
          genuine, sustained failure rather than transient re-enumeration
          noise. If multiple different devices settle-fail within
          `batch_window_ms` of the first one, they're delivered together
          in a single call rather than one call per device (confirmed live:
          a single disable/enable cycle produces multiple, sometimes
          duplicate, transient remove/arrival pairs within ~500ms-1s of
          each other — reacting to every raw event would be noisy and,
          worse, would pop the notification UI more than once per real
          event). Always passes the *current full* set of settled-failed
          device IDs (sorted), not just the newly-added ones, so a caller
          driving a UI list doesn't have to track deltas itself.

      on_recovered(ids: list[str])
          Called once an arrival has settled (no further removal for that
          device within `arrive_settle_ms`) for a device that was
          previously in the failed set. Passes the *remaining* settled-
          failed set (sorted) — an empty list means full recovery. Not
          batched across devices (recovery has no "multiple popups" risk
          to avoid, and this session's live testing showed recovery
          detection is fast — no reason to add an extra grouping delay).

    `schedule(ms, fn) -> cancel_fn` is injectable purely for unit testing
    the debounce/batch logic without a live Qt event loop; production
    callers should leave it as the default (`_qt_schedule`).
    """

    _REMOVE_SETTLE_MS = 1750  # "sustained" threshold; observed noise settles well under this
    _ARRIVE_SETTLE_MS = 750   # avoids badge/notice flicker on a rapid remove/arrive/remove blip
    _BATCH_WINDOW_MS  = 1000  # groups near-simultaneous multi-device failures into one callback

    def __init__(self, on_failed_batch, on_recovered, *,
                 remove_settle_ms=None, arrive_settle_ms=None,
                 batch_window_ms=None, schedule=None):
        self._on_failed_batch = on_failed_batch
        self._on_recovered    = on_recovered
        self._remove_settle_ms = remove_settle_ms if remove_settle_ms is not None else self._REMOVE_SETTLE_MS
        self._arrive_settle_ms = arrive_settle_ms if arrive_settle_ms is not None else self._ARRIVE_SETTLE_MS
        self._batch_window_ms  = batch_window_ms  if batch_window_ms  is not None else self._BATCH_WINDOW_MS
        self._schedule = schedule or _qt_schedule

        self._pending_remove = {}   # instance_id -> cancel_fn (awaiting removal-settle)
        self._pending_arrive = {}   # instance_id -> cancel_fn (awaiting recovery-settle)
        self._failed        = set()  # instance_ids currently considered settled-failed
        self._pending_batch  = set()  # newly-failed ids waiting on the batch window
        self._batch_cancel   = None

    # ------------------------------------------------------------------
    # Raw event intake
    # ------------------------------------------------------------------

    def handle_removal(self, instance_id: str) -> None:
        cancel_arrive = self._pending_arrive.pop(instance_id, None)
        if cancel_arrive is not None:
            cancel_arrive()

        existing_cancel = self._pending_remove.get(instance_id)
        if existing_cancel is not None:
            existing_cancel()

        def _settle():
            self._pending_remove.pop(instance_id, None)
            self._onRemovalSettled(instance_id)

        self._pending_remove[instance_id] = self._schedule(self._remove_settle_ms, _settle)

    def handle_arrival(self, instance_id: str) -> None:
        cancel_remove = self._pending_remove.pop(instance_id, None)
        if cancel_remove is not None:
            # Arrived again before the removal ever settled — a transient
            # blip during the disable/enable transition, not a real
            # failure. Nothing further to do.
            cancel_remove()
            return

        if instance_id not in self._failed:
            return  # not a device we're tracking as failed — ignore

        existing_cancel = self._pending_arrive.get(instance_id)
        if existing_cancel is not None:
            existing_cancel()

        def _settle():
            self._pending_arrive.pop(instance_id, None)
            self._onArrivalSettled(instance_id)

        self._pending_arrive[instance_id] = self._schedule(self._arrive_settle_ms, _settle)

    # ------------------------------------------------------------------
    # Settled events
    # ------------------------------------------------------------------

    def _onRemovalSettled(self, instance_id: str) -> None:
        if instance_id in self._failed:
            return
        self._failed.add(instance_id)
        self._pending_batch.add(instance_id)
        if self._batch_cancel is None:
            def _fire_batch():
                self._batch_cancel = None
                still_pending = bool(self._pending_batch)
                self._pending_batch.clear()
                if not still_pending:
                    # Everything that scheduled this batch window recovered
                    # (settled-arrived) before the window elapsed — firing
                    # now would deliver a stale/empty batch for a failure
                    # that's already resolved. Skip the callback entirely.
                    return
                self._on_failed_batch(sorted(self._failed))
            self._batch_cancel = self._schedule(self._batch_window_ms, _fire_batch)

    def _onArrivalSettled(self, instance_id: str) -> None:
        if instance_id not in self._failed:
            return
        self._failed.discard(instance_id)
        self._pending_batch.discard(instance_id)
        self._on_recovered(sorted(self._failed))

    # ------------------------------------------------------------------
    # Introspection (used by tests / callers that need current state)
    # ------------------------------------------------------------------

    @property
    def failed_ids(self):
        return sorted(self._failed)
