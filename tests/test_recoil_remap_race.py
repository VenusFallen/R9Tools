"""
Unit test for the RecoilEngine remapper race-condition fix (mechanism B).

Regression target: _tryRemap()'s down-event write (`self._remapActive[sig]
= to_input`) and up-event pop (`self._remapActive.pop(sig, None)`) used to
happen outside `self._lock`, while updateSettings() read+cleared the same
dict *under* the lock as part of its settings-change cleanup. If a settings
change (from any panel) landed between a remapped key's physical down-event
and its physical up-event, updateSettings() would clear _remapActive early.
When the physical key-up then arrived, _tryRemap() found nothing to pop,
returned False, and the ORIGINAL (never-pressed-down, since it was
suppressed) key's raw up-event got forwarded to the game — a stray
key-up with no matching down-event.

The fix:
  1. All reads/writes/mutations of `self._remapActive` are now consistently
     protected by `self._lock`.
  2. updateSettings() moves any sig it force-clears into a new
     `self._remapPendingRelease` set (in addition to the pre-existing
     behavior of synthesizing an up-event for whatever the remap target
     was, so the game doesn't end up with a target key that got a
     down-event but never gets a matching up-event).
  3. _tryRemap() checks `_remapPendingRelease` first, before the
     enabled/window-filter checks (since the very settings change that
     orphaned the sig may have disabled the remapper or changed the
     window filter), and swallows that stray physical up-event instead of
     passing it through raw.

This test doesn't start any interception threads, real hardware, or the
`interception` driver — it drives _tryRemap()/updateSettings() directly
against constructed settings dicts, with `inter=None` so _sendSynthesized()
is a safe no-op (self._kbDevice / self._msDevice default to None).
Timing-dependent thread interleaving itself (the actual race window) isn't
reproduced here — see the report for what still needs manual verification.

Run with: python -m unittest tests.test_recoil_remap_race -v
       or: python -m pytest tests/test_recoil_remap_race.py -v
"""
import sys
import os
import copy
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recoil import RecoilEngine

_FROM_KEY = {"type": "key", "code": 30, "e0": False}   # scancode 30 = 'A'
_TO_KEY   = {"type": "key", "code": 48, "e0": False}   # scancode 48 = 'B'
_SIG      = ("key", _FROM_KEY["code"], _FROM_KEY["e0"])


def _base_settings():
    return {
        "theme": "Dark",
        "window_filter": "",
        "recoil": {
            "enabled": False,
            "trigger_keys": [],
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
        "remapper": {
            "enabled": True,
            "mappings": [{"from": dict(_FROM_KEY), "to": dict(_TO_KEY)}],
        },
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


class TestRecoilRemapRace(unittest.TestCase):

    def test_normal_down_up_cycle_no_leftover_state(self):
        """Sanity check with no settings change in between: down activates
        the remap, up consumes it cleanly, nothing lingers either dict."""
        eng = RecoilEngine(_base_settings())

        suppressed_down = eng._tryRemap(_SIG, False, None)
        self.assertTrue(suppressed_down)
        self.assertIn(_SIG, eng._remapActive)

        suppressed_up = eng._tryRemap(_SIG, True, None)
        self.assertTrue(suppressed_up)
        self.assertNotIn(_SIG, eng._remapActive)
        self.assertEqual(eng._remapPendingRelease, set())

    def test_settings_change_while_held_moves_sig_to_pending_release(self):
        """The core race: a settings change lands between the physical
        down and up. updateSettings() must clear _remapActive AND record
        the sig as pending release, not just silently drop it."""
        eng = RecoilEngine(_base_settings())

        self.assertTrue(eng._tryRemap(_SIG, False, None))
        self.assertIn(_SIG, eng._remapActive)

        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"  # unrelated change
        eng.updateSettings(updated)

        self.assertNotIn(_SIG, eng._remapActive)
        self.assertIn(_SIG, eng._remapPendingRelease)

    def test_stray_up_after_settings_change_is_swallowed_not_forwarded(self):
        """This is the actual bug: once updateSettings() has force-cleared
        an active remap, the eventual real physical up-event for that sig
        must still be suppressed (return True) so the original never-
        pressed key's up-event doesn't get forwarded to the game raw.
        Before the fix, this returned False."""
        eng = RecoilEngine(_base_settings())

        eng._tryRemap(_SIG, False, None)
        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"
        eng.updateSettings(updated)

        suppressed = eng._tryRemap(_SIG, True, None)
        self.assertTrue(suppressed, "stray up-event after settings-change "
                                     "cleanup must be suppressed, not passed "
                                     "through to the game")
        # One-shot: consumed, not left around to swallow a future,
        # unrelated down/up cycle for the same physical key.
        self.assertNotIn(_SIG, eng._remapPendingRelease)

    def test_pending_release_swallowed_even_if_remapper_now_disabled(self):
        """The settings change that orphaned the sig might itself be the
        one that disabled the remapper entirely. The stray up-event still
        needs to be swallowed rather than passed through as a raw
        keystroke with no matching down."""
        eng = RecoilEngine(_base_settings())

        eng._tryRemap(_SIG, False, None)
        updated = copy.deepcopy(eng._fullSettings)
        updated["remapper"]["enabled"] = False
        eng.updateSettings(updated)

        self.assertIn(_SIG, eng._remapPendingRelease)
        suppressed = eng._tryRemap(_SIG, True, None)
        self.assertTrue(suppressed)

    def test_unrelated_key_up_not_affected_by_pending_release(self):
        """Swallowing the orphaned sig's up-event must not accidentally
        suppress up-events for other, unrelated keys that were never
        remapped in the first place."""
        eng = RecoilEngine(_base_settings())

        eng._tryRemap(_SIG, False, None)
        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"
        eng.updateSettings(updated)

        other_sig = ("key", 999, False)  # never remapped
        suppressed = eng._tryRemap(other_sig, True, None)
        self.assertFalse(suppressed)

    def test_settings_change_with_nothing_held_is_a_no_op(self):
        """No remap active at settings-change time: pending_release stays
        empty and nothing spurious gets suppressed later."""
        eng = RecoilEngine(_base_settings())
        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"

        eng.updateSettings(updated)

        self.assertEqual(eng._remapPendingRelease, set())
        self.assertFalse(eng._tryRemap(_SIG, True, None))


if __name__ == "__main__":
    unittest.main()
