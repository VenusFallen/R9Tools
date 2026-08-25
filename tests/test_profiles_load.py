"""
Unit tests for profiles.py's loadProfile() mid-session profile switch
behavior.

loadProfile() forces off input-affecting features (recoil, remapper,
rapidfire, macros, toggles) so a binding for one game doesn't silently carry
over active into a different profile. Purely-visual Overlay-panel toggles
(crosshair, module indicator, stats) are NOT input-affecting, so they carry
over from the profile's own saved `enabled` state instead.

Uses a temp file for profiles.PROFILES_FILE so save() never touches the
real user profiles.json on disk.

Run with: python -m unittest tests.test_profiles_load -v
       or: python -m pytest tests/test_profiles_load.py -v
"""
import sys
import os
import copy
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import profiles as prof


def _profile_with_everything_on() -> dict:
    """A full profile settings block with every toggle (visual and
    input-affecting) turned on, to exercise loadProfile()'s split
    behavior in one shot."""
    settings = copy.deepcopy(prof._DEFAULT_SETTINGS)
    settings["recoil"]["enabled"]    = True
    settings["remapper"]["enabled"]  = True
    settings["rapidfire"]["enabled"] = True
    settings["crosshair"]["enabled"] = True
    settings["indicator"]["enabled"] = True
    settings["stats"]["enabled"]     = True
    settings["macros"] = [
        {"name": "m1", "enabled": True, "humanize": False, "mode": "once", "actions": []},
    ]
    settings["toggles"] = [
        {"name": "t1", "enabled": True, "type": "mouse", "button": "mouse_x1"},
    ]
    return settings


class LoadProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_profiles_file = prof.PROFILES_FILE
        prof.PROFILES_FILE = os.path.join(self._tmp.name, "profiles.json")

        self.data = {
            "active": prof.DEFAULT_NAME,
            "profiles": {
                prof.DEFAULT_NAME: copy.deepcopy(prof._DEFAULT_SETTINGS),
                "Game A": _profile_with_everything_on(),
            },
        }

    def tearDown(self):
        prof.PROFILES_FILE = self._orig_profiles_file
        self._tmp.cleanup()

    def test_unknown_profile_returns_none(self):
        self.assertIsNone(prof.loadProfile(self.data, "Nonexistent"))

    def test_visual_overlay_toggles_carry_over(self):
        settings = prof.loadProfile(self.data, "Game A")
        self.assertIsNotNone(settings)
        self.assertTrue(settings["crosshair"]["enabled"])
        self.assertTrue(settings["indicator"]["enabled"])
        self.assertTrue(settings["stats"]["enabled"])

    def test_input_affecting_features_still_forced_off(self):
        settings = prof.loadProfile(self.data, "Game A")
        self.assertIsNotNone(settings)
        self.assertFalse(settings["recoil"]["enabled"])
        self.assertFalse(settings["remapper"]["enabled"])
        self.assertFalse(settings["rapidfire"]["enabled"])
        for macro in settings["macros"]:
            self.assertFalse(macro["enabled"])
        for tog in settings["toggles"]:
            self.assertFalse(tog["enabled"])

    def test_active_pointer_updated(self):
        prof.loadProfile(self.data, "Game A")
        self.assertEqual(self.data["active"], "Game A")

    def test_old_profile_missing_overlay_blocks_backfills_defaults(self):
        # Simulate a profile saved before stats/indicator existed.
        legacy = copy.deepcopy(prof._DEFAULT_SETTINGS)
        del legacy["stats"]
        del legacy["indicator"]
        self.data["profiles"]["Legacy"] = legacy

        settings = prof.loadProfile(self.data, "Legacy")
        self.assertIsNotNone(settings)
        self.assertIn("stats", settings)
        self.assertIn("indicator", settings)


if __name__ == "__main__":
    unittest.main()
