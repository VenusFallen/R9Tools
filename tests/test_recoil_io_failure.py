"""
Unit tests for RecoilEngine mid-session Interception I/O failure detection.

Regression target: before this, _listenLoop()'s per-iteration try/except
swallowed every exception (by design, to survive a single bad stroke/
handler bug) with no distinction between "one bad stroke" and "the driver
actually died and every future iteration will fail too". This exercises
the new consecutive-I/O-failure counter (_onInterceptionIoFailure()) that
flips inputEngineFailed and fires setInputFailedCallback() once the
driver's own device I/O (await_input/receive/send) has failed
_INTERCEPTION_IO_FAILURE_THRESHOLD times in a row.

These tests call _onInterceptionIoFailure() and manipulate
_consecutiveIoFailures directly rather than spinning up real
interception.Interception()/threads — no hardware, no background thread,
just the bookkeeping logic itself.

Run with: python -m unittest tests.test_recoil_io_failure -v
       or: python -m pytest tests/test_recoil_io_failure.py -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recoil import RecoilEngine


def _base_settings():
    return {
        "theme": "Dark",
        "window_filter": "",
        "recoil": {
            "enabled": True,
            "trigger_keys": ["mouse_left"],
            "humanize": False,
            "interval_ms": 10,
            "weapons": [{"strength_y": 5}],
        },
        "crosshair": {
            "enabled": False, "style": "cross", "color": "green",
            "size": 10, "thickness": 2, "gap": 3, "outline_size": 1,
        },
        "hotkeys": {
            "overlay_toggle":       {"code": 82, "e0": True},
            "recoil_toggle":        {"code": 68, "e0": False},
            "recoil_strength_down": {"code": 26, "e0": False},
            "recoil_strength_up":   {"code": 27, "e0": False},
            "quit":                 {"code": 83, "e0": True},
        },
        "remapper": {"enabled": False, "mappings": []},
        "rapidfire": {
            "enabled": False,
            "trigger_keys": [],
            "slot_keys": [],
            "interval_ms": 100,
            "humanize": False,
        },
        "indicator": {"enabled": True, "position": "below_crosshair"},
        "macros": [],
        "stats": {"enabled": False, "corner": "top_right", "update_rate_hz": 1,
                   "show_cpu_usage": True, "show_cpu_temp": True},
    }


class TestInterceptionIoFailureDetection(unittest.TestCase):

    def setUp(self):
        self.eng = RecoilEngine(_base_settings())
        self.callback_calls = 0
        self.eng.setInputFailedCallback(self._record_callback)

    def _record_callback(self):
        self.callback_calls += 1

    def test_threshold_crossing_flips_flag_and_fires_once(self):
        """Exactly _INTERCEPTION_IO_FAILURE_THRESHOLD consecutive failures
        must flip inputEngineFailed and fire the callback exactly once —
        further failures after that must not fire it again."""
        threshold = self.eng._INTERCEPTION_IO_FAILURE_THRESHOLD

        for i in range(1, threshold):
            self.eng._onInterceptionIoFailure()
            self.assertFalse(
                self.eng.inputEngineFailed,
                f"flipped early at failure {i}/{threshold}",
            )
            self.assertEqual(self.callback_calls, 0)

        # The threshold-th consecutive failure crosses the line.
        self.eng._onInterceptionIoFailure()
        self.assertTrue(self.eng.inputEngineFailed)
        self.assertEqual(self.callback_calls, 1)

        # Further consecutive failures (simulating a genuinely dead
        # driver where every subsequent iteration keeps failing) must not
        # re-fire the callback — the UI's popup is designed to appear
        # exactly once per failure event.
        for _ in range(10):
            self.eng._onInterceptionIoFailure()
        self.assertTrue(self.eng.inputEngineFailed)
        self.assertEqual(self.callback_calls, 1)

    def test_below_threshold_does_not_trigger(self):
        """A burst of failures that never reaches the threshold must not
        flip the flag or fire the callback at all."""
        threshold = self.eng._INTERCEPTION_IO_FAILURE_THRESHOLD

        for _ in range(threshold - 1):
            self.eng._onInterceptionIoFailure()

        self.assertFalse(self.eng.inputEngineFailed)
        self.assertEqual(self.callback_calls, 0)

    def test_success_reset_prevents_false_positive_across_interspersed_failures(self):
        """Failures interspersed with successful I/O (which resets the
        streak back to 0, exactly like _listenLoop does on a clean
        await_input timeout or a successful receive()) must never
        accumulate toward the threshold, no matter how many total
        failures occur over the engine's lifetime."""
        threshold = self.eng._INTERCEPTION_IO_FAILURE_THRESHOLD

        for _ in range(50):
            # Almost-but-not-quite enough consecutive failures to trip...
            for _ in range(threshold - 1):
                self.eng._onInterceptionIoFailure()
            # ...then a successful I/O call resets the streak, exactly as
            # _listenLoop does after await_input()/receive() succeed.
            self.eng._consecutiveIoFailures = 0

        self.assertFalse(self.eng.inputEngineFailed)
        self.assertEqual(self.callback_calls, 0)

    def test_callback_exception_does_not_prevent_flag_flip(self):
        """A raising callback must not stop inputEngineFailed from being
        set — mirrors the same guard already used on the startup
        bring-up-failure path."""
        def _raising_callback():
            raise RuntimeError("boom")

        eng = RecoilEngine(_base_settings())
        eng.setInputFailedCallback(_raising_callback)
        threshold = eng._INTERCEPTION_IO_FAILURE_THRESHOLD

        for _ in range(threshold):
            eng._onInterceptionIoFailure()

        self.assertTrue(eng.inputEngineFailed)


if __name__ == "__main__":
    unittest.main()
