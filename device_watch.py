"""
device_watch.py — WM_DEVICECHANGE-based detection of a keyboard/mouse
device being disabled/disconnected mid-session.

Why this exists: when a keyboard/mouse device is disabled via Device
Manager / `Disable-PnpDevice` mid-session, Interception's I/O calls
(await_input/receive/send) don't throw — they just silently stop
returning input, indistinguishable from the user not touching the device.
recoil.py's I/O-exception counter (RecoilEngine._onInterceptionIoFailure)
can't see this at all, but pushed OS device-change notifications can:
measured ~1.0s detection vs ~1.8s for 0.5s-interval `Get-PnpDevice`
polling, and ~23ms recovery detection after re-enable.

This module owns two independent, ctypes-only pieces:
  - Win32 plumbing to register for + parse WM_DEVICECHANGE device-interface
    notifications (register_device_notifications, parse_device_change,
    WM_DEVICECHANGE). No Qt dependency here — panel_window.py's
    _DeviceChangeFilter is what hooks this into the app's Qt message pump
    and owns the HWND.
  - DeviceFailureWatcher: a QTimer-driven (but injectable, see `schedule`
    param) debounce/batch state machine that turns noisy raw arrival/
    removal events into two clean callbacks: on_failed_batch(ids) and
    on_recovered(ids). Kept separate from the Win32 plumbing so it's
    unit-testable without ctypes memory reads or a live HWND (see
    tests/test_device_watch.py).

CRITICAL ctypes gotcha: every unset restype defaults to 32-bit c_int.
Pointer/handle-returning Win32 functions on this 64-bit process will
overflow and crash unless .restype/.argtypes are set explicitly on every
function used here.
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

# Well-known device interface GUIDs (ntddkbd.h / ntddmou.h / hidclass.h).
GUID_DEVINTERFACE_MOUSE    = "{378de44c-56ef-11d1-bc8c-00a0c91405dd}"
GUID_DEVINTERFACE_KEYBOARD = "{884b96c3-56ef-11d1-bc8c-00a0c91405dd}"

# GUID_DEVINTERFACE_HID ("{4d1e55b2-f16f-11cf-88cb-001111000030}") is
# deliberately NOT registered here. It was carried over early on as a
# defensive hedge alongside GUID_DEVINTERFACE_MOUSE, but live testing
# confirmed real keyboards/mice (including composite ones exposing extra
# HID collections, e.g. a keyboard's volume wheel) always publish the
# specific Mouse/Keyboard interfaces on their own — the generic HID
# interface added nothing there. Worse, it's not specific to keyboards/
# mice at all: any HID-class device matches it, including gamepads, so a
# wireless gamepad powering off from its own idle timeout would fire the
# exact same DBT_DEVICEREMOVECOMPLETE path as a genuine keyboard/mouse
# disconnect and trigger a false "input lost" prompt. Registering only
# for the specific interfaces eliminates that false-positive class at the
# source instead of needing a runtime instance-ID filter on this
# real-time path.
_ALL_GUIDS = (GUID_DEVINTERFACE_MOUSE, GUID_DEVINTERFACE_KEYBOARD)

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
    """Register for arrival/removal notifications on the mouse and keyboard
    device interface classes (see _ALL_GUIDS for why the generic HID class
    is deliberately excluded) against the given real HWND (see
    panel_window.py's _TopBarWindow, which reuses its own winId()).

    Returns the successfully-registered notification handles; not
    explicitly torn down anywhere (both this and the WM_DISPLAYCHANGE
    filter live for the app's lifetime). Registration failures are logged
    and skipped rather than raised, so one bad GUID can't take down the
    other."""
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
    -InstanceId` expect (e.g. r"HID\\VID_046D&PID_C24A&MI_00\\8&D4D6DFF&0&0000"):
    strips the leading "\\\\?\\" prefix, takes the first 3 '#'-delimited
    segments (discarding the trailing device-interface-GUID segment),
    replaces '#' with '\\', and uppercases. Returns None for anything that
    doesn't have the expected shape (fewer than 4 segments) rather than
    guessing."""
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
    `fn` after `ms` milliseconds via a single-shot QTimer, returning a
    cancel callable. PySide6 is imported lazily so this module (and
    DeviceFailureWatcher's debounce logic) stays unit-testable without a
    live QApplication (see tests/test_device_watch.py).

    The QTimer is kept alive via the module-level `_live` set — an
    un-parented QTimer with no other Python references can otherwise be
    garbage-collected before it fires, a known PySide gotcha."""
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
          device within `remove_settle_ms`), treating it as a genuine
          failure rather than transient re-enumeration noise. Devices that
          settle-fail within `batch_window_ms` of each other are delivered
          together in one call, since a single disable/enable cycle can
          produce multiple duplicate remove/arrival pairs within ~500ms-1s
          and reacting to every raw event would pop the notification UI
          more than once per real event. Always passes the current full
          sorted set of settled-failed device IDs, not just the new ones,
          so a caller driving a UI list doesn't have to track deltas.

      on_recovered(ids: list[str])
          Called once an arrival has settled (no further removal within
          `arrive_settle_ms`) for a previously-failed device. Passes the
          remaining settled-failed set (sorted); an empty list means full
          recovery. Not batched across devices — recovery detection is
          fast enough that an extra grouping delay isn't needed.

    `schedule(ms, fn) -> cancel_fn` is injectable for unit testing the
    debounce/batch logic without a live Qt event loop; production callers
    should leave it as the default (`_qt_schedule`).
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
