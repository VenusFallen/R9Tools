"""
Unit test for the RecoilEngine.updateSettings() reset-scope fix.

Regression target: updateSettings() used to unconditionally reset
_rfArmed / _rfFireHeld / _rfSuppressing / _held / _activeWeaponIdx on
*every* call, but it's invoked from a single settings-changed callback
shared by every panel in the UI. That meant nudging an unrelated control
(crosshair color, a macro edit, etc.) mid-match silently disarmed Rapid
Fire. This test doesn't start any interception threads or touch real
hardware — it drives updateSettings() directly against constructed
settings dicts.

Run with: python -m unittest tests.test_recoil_update_settings -v
       or: python -m pytest tests/test_recoil_update_settings.py -v
"""
import sys
import os
import copy
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
            "weapons": [{"strength_y": 5}, {"strength_y": 8}, {"strength_y": 3}],
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
            "enabled": True,
            "trigger_keys": ["mouse_left"],
            "slot_keys": [{"type": "mouse", "button": "mouse_x1", "enabled": True}],
            "interval_ms": 100,
            "humanize": False,
        },
        "indicator": {"enabled": True, "position": "below_crosshair"},
        "macros": [],
        "stats": {"enabled": False, "corner": "top_right", "update_rate_hz": 1,
                   "show_cpu_usage": True, "show_cpu_temp": True},
    }


def _armed_engine():
    """Build an engine and put it into an "armed + firing + held" state,
    simulating being mid-match with RF engaged."""
    eng = RecoilEngine(_base_settings())
    eng._rfArmed = True
    eng._lastRfArmToggleTime = 12345.0
    eng._rfFireHeld = True
    eng._rfSuppressing = True
    eng._held.add("mouse_left")
    eng._activeWeaponIdx = 2
    return eng


class TestRecoilUpdateSettingsResetScope(unittest.TestCase):

    def test_unrelated_setting_change_does_not_disarm(self):
        """(a) Changing something unrelated (crosshair color) must not
        touch RF armed/held/suppressing state at all."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"

        eng.updateSettings(updated)  # full_reset defaults False

        self.assertTrue(eng._rfArmed)
        self.assertEqual(eng._lastRfArmToggleTime, 12345.0)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)
        self.assertIn("mouse_left", eng._held)
        self.assertEqual(eng._activeWeaponIdx, 2)

    def test_unrelated_macro_edit_does_not_disarm(self):
        """Same as above but simulating a macro-panel edit (a different
        module entirely) to confirm this isn't just crosshair-specific."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["macros"].append({
            "enabled": True, "mode": "once",
            "trigger": {"type": "key", "code": 30, "e0": False},
            "actions": [], "humanize": False,
        })

        eng.updateSettings(updated)

        self.assertTrue(eng._rfArmed)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)
        self.assertEqual(eng._activeWeaponIdx, 2)

    def test_recoil_cosmetic_change_does_not_disarm(self):
        """A cosmetic recoil/RF tweak (humanize toggle) shouldn't invalidate
        armed/held state either — only slot_keys/trigger_keys/weapons are
        structural enough to matter."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["recoil"]["humanize"] = True
        updated["rapidfire"]["interval_ms"] = 50

        eng.updateSettings(updated)

        self.assertTrue(eng._rfArmed)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)
        self.assertEqual(eng._activeWeaponIdx, 2)

    def test_weapon_list_shrink_clamps_active_index(self):
        """(b) If the weapon list shrinks below the current active index,
        that index must be reset even on the routine (non-full) path —
        but RF armed/fire state should be untouched since slot_keys /
        trigger_keys didn't change."""
        eng = _armed_engine()
        self.assertEqual(eng._activeWeaponIdx, 2)
        updated = copy.deepcopy(eng._fullSettings)
        updated["recoil"]["weapons"] = [{"strength_y": 5}]  # only 1 left, idx 2 now OOB

        eng.updateSettings(updated)

        self.assertEqual(eng._activeWeaponIdx, 0)
        self.assertTrue(eng._rfArmed)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)

    def test_weapon_list_still_covers_index_no_reset(self):
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["recoil"]["weapons"].append({"strength_y": 1})  # grew, idx 2 still valid

        eng.updateSettings(updated)

        self.assertEqual(eng._activeWeaponIdx, 2)

    def test_slot_keys_change_resets_armed_state_only(self):
        """Changing RF's own arm/disarm bindings is a structural change to
        RF itself, so _rfArmed legitimately needs to reset — but this
        shouldn't touch the (unrelated) fire/suppressing state."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["rapidfire"]["slot_keys"] = [
            {"type": "mouse", "button": "mouse_x2", "enabled": True}
        ]

        eng.updateSettings(updated)

        self.assertFalse(eng._rfArmed)
        self.assertIsNone(eng._lastRfArmToggleTime)
        # fire/suppressing state tracked separately from arm state
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)

    def test_trigger_keys_change_resets_fire_state_only(self):
        """Changing RF's fire-trigger key bindings invalidates the
        held/suppressing state (it would otherwise get stuck), but
        shouldn't touch the unrelated armed state."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)
        updated["rapidfire"]["trigger_keys"] = ["mouse_right"]

        eng.updateSettings(updated)

        self.assertFalse(eng._rfFireHeld)
        self.assertFalse(eng._rfSuppressing)
        self.assertTrue(eng._rfArmed)
        self.assertEqual(eng._lastRfArmToggleTime, 12345.0)

    def test_full_reset_true_wipes_everything_for_profile_load(self):
        """(c) Profile load/switch uses full_reset=True and must fully
        reset armed/held/suppressing/weapon-index state exactly like the
        old unconditional behavior, even if nothing "structural" changed
        by the diff-based rules (e.g. switching to a profile with
        identical rapidfire/weapons config)."""
        eng = _armed_engine()
        updated = copy.deepcopy(eng._fullSettings)  # identical content, new profile

        eng.updateSettings(updated, full_reset=True)

        self.assertFalse(eng._rfArmed)
        self.assertIsNone(eng._lastRfArmToggleTime)
        self.assertFalse(eng._rfFireHeld)
        self.assertFalse(eng._rfSuppressing)
        self.assertEqual(eng._activeWeaponIdx, 0)
        self.assertEqual(eng._held, set())


if __name__ == "__main__":
    unittest.main()
