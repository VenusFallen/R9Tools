"""
Unit tests for device_watch.py's pure logic: the DEV_BROADCAST_DEVICEINTERFACE
symlink -> PnP instance-ID parser, and the DeviceFailureWatcher debounce/
batch state machine.

Neither of these needs a live HWND, ctypes memory read, or Qt event loop —
_symlink_to_instance_id is plain string manipulation, and
DeviceFailureWatcher's `schedule` param is injected here with a fake,
manually-advanced scheduler (see _FakeScheduler) instead of the real
QTimer-backed default, mirroring this test suite's existing convention
(test_recoil_io_failure.py) of calling internal bookkeeping methods/hooks
directly rather than spinning up real timers/threads/hardware.

The Win32 registration/parsing plumbing that DOES need a real HWND/live
memory (register_device_notifications, parse_device_change's ctypes reads)
is NOT covered here — that's OS-integration surface with no realistic
automated coverage, flagged for manual/QA verification same as the rest of
this feature.

Run with: python -m pytest tests/test_device_watch.py -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from device_watch import (
    DeviceFailureWatcher,
    _symlink_to_instance_id,
    _ALL_GUIDS,
    GUID_DEVINTERFACE_MOUSE,
    GUID_DEVINTERFACE_KEYBOARD,
)


class TestRegisteredGuidsExcludeGenericHid(unittest.TestCase):
    """Regression test for the false "input lost" prompt a wireless
    gamepad's own idle-timeout power-off could trigger: the generic HID
    device-interface class ("{4d1e55b2-f16f-11cf-88cb-001111000030}")
    matches ANY HID-class device, not just keyboards/mice, so a gamepad
    disconnect fired the exact same DBT_DEVICEREMOVECOMPLETE path as a
    genuine keyboard/mouse disconnect.

    register_device_notifications() only ever registers the GUIDs in
    _ALL_GUIDS, and parse_device_change() has no GUID-class-specific
    filtering of its own (it parses dbcc_name into an instance ID
    regardless of dbcc_classguid) — so keeping the generic HID GUID out of
    _ALL_GUIDS is sufficient on its own to guarantee a gamepad-style
    removal is never observed at all, without needing any runtime
    instance-ID filtering on this real-time path."""

    def test_generic_hid_guid_not_registered(self):
        self.assertNotIn("{4d1e55b2-f16f-11cf-88cb-001111000030}", _ALL_GUIDS)

    def test_only_mouse_and_keyboard_guids_registered(self):
        self.assertEqual(
            set(_ALL_GUIDS),
            {GUID_DEVINTERFACE_MOUSE, GUID_DEVINTERFACE_KEYBOARD},
        )


class TestSymlinkToInstanceId(unittest.TestCase):

    def test_confirmed_live_example(self):
        """Exact symlink captured during live testing this session must
        parse to the exact instance ID Get-PnpDevice/Disable-PnpDevice
        -InstanceId expect."""
        symlink = (
            r"\\?\HID#VID_046D&PID_C24A&MI_00#8&d4d6dff&0&0000"
            r"#{4d1e55b2-f16f-11cf-88cb-001111000030}"
        )
        self.assertEqual(
            _symlink_to_instance_id(symlink),
            r"HID\VID_046D&PID_C24A&MI_00\8&D4D6DFF&0&0000",
        )

    def test_uppercases_result(self):
        symlink = r"\\?\hid#vid_1234&pid_5678#0&abc123&0&0001#{guid}"
        result = _symlink_to_instance_id(symlink)
        self.assertEqual(result, result.upper())

    def test_too_few_segments_returns_none(self):
        # Only 2 '#'-delimited segments — not enough to discard a trailing
        # GUID segment and still have 3 left.
        self.assertIsNone(_symlink_to_instance_id(r"\\?\HID#VID_1234"))

    def test_no_prefix_still_parses(self):
        # Defensive: even if the leading \\?\ is somehow already stripped,
        # the '#'-splitting logic should still work.
        symlink = r"HID#VID_046D&PID_C24A&MI_00#8&d4d6dff&0&0000#{guid}"
        self.assertEqual(
            _symlink_to_instance_id(symlink),
            r"HID\VID_046D&PID_C24A&MI_00\8&D4D6DFF&0&0000",
        )


class _FakeScheduler:
    """Manually-advanced stand-in for device_watch._qt_schedule. Records
    every (ms, fn) scheduled and lets tests fire them on demand, without a
    live QTimer/event loop. Firing an entry can itself schedule new
    entries (e.g. the batch timer firing from within a removal-settle
    callback) — fire_all() keeps draining until nothing new/uncancelled is
    left, single-pass-per-call semantics aren't needed for these tests."""

    def __init__(self):
        self._entries = []  # list of dict(ms=, fn=, cancelled=, fired=)

    def __call__(self, ms, fn):
        entry = {"ms": ms, "fn": fn, "cancelled": False, "fired": False}
        self._entries.append(entry)

        def _cancel():
            entry["cancelled"] = True

        return _cancel

    def pending_count(self):
        return sum(1 for e in self._entries if not e["cancelled"] and not e["fired"])

    def fire_matching(self, ms):
        """Fire exactly ONE still-pending entry scheduled with this ms
        delay, leaving every other pending entry untouched. Unlike
        fire_all(), this lets a test reproduce real-world interleavings
        where multiple timers (e.g. a batch timer and a since-scheduled
        recovery-settle timer) are pending at once and fire in a specific
        order rather than all being drained in one pass. Requires the
        caller to use ms values that unambiguously identify the timer of
        interest (DeviceFailureWatcher's three settle/batch delays are
        distinct by default)."""
        for e in self._entries:
            if not e["cancelled"] and not e["fired"] and e["ms"] == ms:
                e["fired"] = True
                e["fn"]()
                return
        raise AssertionError(f"no pending scheduled entry with ms={ms}")

    def fire_all(self):
        while True:
            todo = [e for e in self._entries if not e["cancelled"] and not e["fired"]]
            if not todo:
                return
            e = todo[0]
            e["fired"] = True
            e["fn"]()


class TestDeviceFailureWatcher(unittest.TestCase):

    def setUp(self):
        self.failed_calls = []
        self.recovered_calls = []
        self.scheduler = _FakeScheduler()
        self.watcher = DeviceFailureWatcher(
            on_failed_batch=lambda ids: self.failed_calls.append(ids),
            on_recovered=lambda ids: self.recovered_calls.append(ids),
            schedule=self.scheduler,
        )

    def test_single_removal_settles_to_one_failed_batch(self):
        self.watcher.handle_removal("DEV1")
        self.assertEqual(self.failed_calls, [])  # not settled yet
        self.scheduler.fire_all()
        self.assertEqual(self.failed_calls, [["DEV1"]])

    def test_transient_removal_then_quick_arrival_never_fires(self):
        """A removal immediately followed by an arrival (before the
        removal-settle timer fires) must be treated as noise, not a real
        failure — mirrors the observed re-enumeration blips during a
        disable/enable cycle."""
        self.watcher.handle_removal("DEV1")
        self.watcher.handle_arrival("DEV1")
        self.scheduler.fire_all()
        self.assertEqual(self.failed_calls, [])
        self.assertEqual(self.recovered_calls, [])
        self.assertEqual(self.watcher.failed_ids, [])

    def test_multiple_devices_settling_close_together_batch_into_one_call(self):
        """Two different devices failing within the same batch window must
        produce exactly one on_failed_batch call carrying both — not two
        separate popups."""
        self.watcher.handle_removal("DEV1")
        self.watcher.handle_removal("DEV2")
        self.scheduler.fire_all()
        self.assertEqual(len(self.failed_calls), 1)
        self.assertEqual(self.failed_calls[0], ["DEV1", "DEV2"])

    def test_recovery_after_settled_failure_reports_remaining_set(self):
        self.watcher.handle_removal("DEV1")
        self.watcher.handle_removal("DEV2")
        self.scheduler.fire_all()
        self.assertEqual(self.watcher.failed_ids, ["DEV1", "DEV2"])

        self.watcher.handle_arrival("DEV1")
        self.scheduler.fire_all()
        self.assertEqual(self.recovered_calls, [["DEV2"]])
        self.assertEqual(self.watcher.failed_ids, ["DEV2"])

        self.watcher.handle_arrival("DEV2")
        self.scheduler.fire_all()
        self.assertEqual(self.recovered_calls[-1], [])
        self.assertEqual(self.watcher.failed_ids, [])

    def test_arrival_for_untracked_device_is_ignored(self):
        """An arrival for a device that was never in the failed set (e.g.
        a totally unrelated device being plugged in) must not schedule
        anything or call back."""
        self.watcher.handle_arrival("DEV_NEVER_FAILED")
        self.assertEqual(self.scheduler.pending_count(), 0)
        self.assertEqual(self.recovered_calls, [])

    def test_repeat_removal_settle_for_already_failed_device_is_a_noop(self):
        self.watcher.handle_removal("DEV1")
        self.scheduler.fire_all()
        self.assertEqual(self.failed_calls, [["DEV1"]])

        self.watcher.handle_removal("DEV1")
        self.scheduler.fire_all()
        # Still failed, but must not fire a second, redundant batch call.
        self.assertEqual(self.failed_calls, [["DEV1"]])

    def test_flapping_arrival_then_removal_before_arrival_settles_stays_failed(self):
        """Once failed, an arrival that itself gets reversed by another
        removal before the recovery-settle timer fires must NOT report a
        recovery — the device is still failed."""
        self.watcher.handle_removal("DEV1")
        self.scheduler.fire_all()
        self.assertEqual(self.watcher.failed_ids, ["DEV1"])

        self.watcher.handle_arrival("DEV1")   # recovery-settle timer scheduled
        self.watcher.handle_removal("DEV1")   # reversed before it could settle
        self.scheduler.fire_all()

        self.assertEqual(self.recovered_calls, [])
        self.assertEqual(self.watcher.failed_ids, ["DEV1"])

    def test_full_recovery_before_batch_fires_suppresses_stale_empty_batch(self):
        """Reproduces the real-world interleaving QA found (NOT visible via
        fire_all(), which drains every pending timer to completion before
        the test issues its next handle_removal/handle_arrival call): a
        device settles as failed, starting the batch-window timer, then
        recovers and its own recovery-settle timer fires BEFORE that
        original batch timer does. When the batch timer finally fires, it
        must be a no-op — not a stale/empty on_failed_batch([]) call — since
        nothing is pending/failed by then."""
        self.watcher.handle_removal("DEV1")
        self.scheduler.fire_matching(self.watcher._remove_settle_ms)
        self.assertEqual(self.failed_calls, [])
        self.assertEqual(self.watcher.failed_ids, ["DEV1"])

        self.watcher.handle_arrival("DEV1")
        self.scheduler.fire_matching(self.watcher._arrive_settle_ms)
        self.assertEqual(self.recovered_calls, [[]])
        self.assertEqual(self.watcher.failed_ids, [])

        # The original batch timer (scheduled back when the removal
        # settled) still fires later. It must NOT deliver a stale/empty
        # batch now that the device has already fully recovered.
        self.scheduler.fire_matching(self.watcher._batch_window_ms)
        self.assertEqual(self.failed_calls, [])


if __name__ == "__main__":
    unittest.main()
