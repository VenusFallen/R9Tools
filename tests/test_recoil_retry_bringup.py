"""
Unit tests for RecoilEngine.retryBringUp() — the manual "retry input
capture after a startup failure" path added for the "Try to Reconnect"
UI feature.

Background: if _listenLoop() exhausts _bringUpInterception()'s retries at
startup, it sets inputEngineFailed=True and returns — the listen thread
exits permanently, with (before this) no way to bring it back without a
full app restart. retryBringUp() lets the UI ask the engine to try again
later, from a background thread, after doing something to fix the
underlying device state (e.g. cycling PnP devices).

These tests mock RecoilEngine._bringUpInterception() (via
unittest.mock.patch.object, matching the style already used in
tests/test_macro_window_filter.py) rather than spinning up a real
interception.Interception() — no hardware required. _applyThread/
_rfThread are swapped for the same no-op _FakeThread stand-in already
used by tests/test_recoil_toggles.py's stop() tests, since retryBringUp()
only concerns _listenThread; _listenThread itself is left as a real
threading.Thread so its actual lifecycle (started by retryBringUp(),
joined by stop()) is genuinely exercised.

Run with: python -m unittest tests.test_recoil_retry_bringup -v
       or: python -m pytest tests/test_recoil_retry_bringup.py -v
"""
import sys
import os
import time
import threading
import unittest
from unittest.mock import patch

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


class _FakeThread:
    """Stand-in for _applyThread/_rfThread so stop() can be exercised
    without ever calling start() — mirrors tests/test_recoil_toggles.py."""
    def join(self, timeout=None):
        pass


class _FakeInterception:
    """Minimal stand-in for a live interception.Interception() context —
    just enough surface for _listenLoop() to idle harmlessly on
    await_input() timeouts (deviceIdx=None branch) until self._running
    flips False, plus a destroy() call for cleanup bookkeeping."""
    def __init__(self):
        self._devices = list(range(20))
        self.destroyed = False

    def await_input(self, timeout_ms):
        time.sleep(0.01)
        return None

    def is_mouse(self, idx):
        return False

    def is_keyboard(self, idx):
        return False

    def destroy(self):
        self.destroyed = True


def _make_engine():
    eng = RecoilEngine(_base_settings())
    eng._applyThread = _FakeThread()
    eng._rfThread = _FakeThread()
    return eng


class TestRetryBringUpSuccess(unittest.TestCase):
    def test_retry_succeeds_and_stop_can_cleanly_join_the_new_thread(self):
        eng = _make_engine()
        # Simulate the post-startup-failure state: start() ran, the
        # original _listenLoop already exhausted its retries and exited.
        eng._running = True
        eng.inputEngineFailed = True
        self.assertFalse(eng._listenThread.is_alive())

        fake_inter = _FakeInterception()
        with patch.object(RecoilEngine, "_bringUpInterception",
                           return_value=fake_inter):
            result = eng.retryBringUp()

        self.assertTrue(result, "retryBringUp() must return True on a "
                                 "genuine successful bring-up")
        self.assertFalse(eng.inputEngineFailed)
        self.assertTrue(
            eng._listenThread.is_alive(),
            "a fresh listen thread must actually be running after success"
        )

        eng.stop()

        self.assertFalse(
            eng._listenThread.is_alive(),
            "stop() must join the NEW thread reference left by "
            "retryBringUp(), not a stale one"
        )
        self.assertTrue(fake_inter.destroyed)

    def test_retry_is_a_noop_when_listen_thread_already_alive(self):
        """Mid-session I/O failure: the listen thread never exited, so
        retryBringUp() must not start a second one on top of it."""
        eng = _make_engine()
        eng._running = True

        started = threading.Event()
        stop_flag = threading.Event()

        def fake_loop():
            started.set()
            while not stop_flag.is_set():
                time.sleep(0.01)

        eng._listenThread = threading.Thread(target=fake_loop, daemon=True)
        eng._listenThread.start()
        started.wait(timeout=1.0)

        eng.inputEngineFailed = True  # e.g. _onInterceptionIoFailure() fired
        with patch.object(RecoilEngine, "_bringUpInterception") as mocked:
            result = eng.retryBringUp()
            mocked.assert_not_called()

        self.assertFalse(result, "must honestly report unhealthy state, "
                                  "not silently no-op into a false True")

        stop_flag.set()
        eng._listenThread.join(timeout=1.0)


class TestRetryBringUpFailure(unittest.TestCase):
    def test_retry_fails_leaves_flag_set_and_starts_no_thread(self):
        eng = _make_engine()
        eng._running = True
        eng.inputEngineFailed = True

        with patch.object(RecoilEngine, "_bringUpInterception",
                           return_value=None):
            result = eng.retryBringUp()

        self.assertFalse(result)
        self.assertTrue(eng.inputEngineFailed)
        self.assertFalse(eng._listenThread.is_alive())

    def test_retry_without_prior_start_returns_false(self):
        """No active session (start() never ran) — nothing to resume."""
        eng = _make_engine()
        eng.inputEngineFailed = True
        self.assertFalse(eng._running)

        with patch.object(RecoilEngine, "_bringUpInterception") as mocked:
            result = eng.retryBringUp()
            mocked.assert_not_called()

        self.assertFalse(result)


class TestRetryBringUpConcurrency(unittest.TestCase):
    def test_concurrent_calls_do_not_start_duplicate_threads(self):
        eng = _make_engine()
        eng._running = True
        eng.inputEngineFailed = True

        call_count = {"n": 0}
        lock = threading.Lock()

        def slow_bring_up(self_):
            with lock:
                call_count["n"] += 1
            time.sleep(0.2)
            return _FakeInterception()

        results = []
        results_lock = threading.Lock()

        def worker():
            r = eng.retryBringUp()
            with results_lock:
                results.append(r)

        with patch.object(RecoilEngine, "_bringUpInterception", slow_bring_up):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            time.sleep(0.05)  # let t1 win the retry lock first
            t2.start()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)

        self.assertEqual(
            call_count["n"], 1,
            "a concurrent second call must bail out immediately instead of "
            "attempting its own bring-up"
        )
        self.assertEqual(sorted(results), [False, True])
        self.assertTrue(eng._listenThread.is_alive())

        eng.stop()
        self.assertFalse(eng._listenThread.is_alive())


if __name__ == "__main__":
    unittest.main()
