"""
Regression tests for the RF weapon-slot "enabled: False" bug.

Bug: settings["rapidfire"]["slot_keys"] entries each independently toggle
the single shared self._rfArmed flag on release (see
RecoilEngine._tryToggleRfArmed_locked). A slot key configured with
enabled=False was previously just skipped entirely -- it never called
_tryToggleRfArmed_locked() at all, which means pressing a "disabled" slot's
key left self._rfArmed completely untouched (whatever it happened to be
from before), rather than guaranteeing RF is off while that weapon is
selected. Concretely: arm RF via weapon 1's (enabled) slot key, then switch
to weapon 2 or 3 (disabled slot keys) -- RF stayed armed/firing until
switching back to weapon 1 and toggling it off there.

Fix: a disabled slot key now calls RecoilEngine._forceRfDisarmed_locked(),
which unconditionally forces self._rfArmed = False (not gated by the
arm-toggle debounce -- idempotent, no flicker risk) and also clears
self._rfFireHeld so any RF fire cycle already in progress stops on the
fire loop's next poll. self._rfSuppressing is deliberately left alone by
the force-disarm path -- see the mid-fire test below, which exercises why.

Applies to all three slot-key identity types: mouse, scroll, and keyboard
(_coreMouseButtonUp, _coreScroll, _applyKeyboardSlotAndWeaponSelect all
mirror the same toggle-vs-force branch).

These tests drive RecoilEngine's core handlers directly with constructed
interception KeyStroke/MouseStroke objects -- no real hardware, no
Interception() driver instance, no listen thread. Follows the same style as
tests/test_recoil_toggles.py and tests/test_recoil_update_settings.py.

Run with: python -m unittest tests.test_recoil_rf_slot_disable -v
       or: python -m pytest tests/test_recoil_rf_slot_disable.py -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interception
from recoil import RecoilEngine


class _FakeDevice:
    def __init__(self):
        self.sent = []

    def send(self, stroke):
        self.sent.append(stroke)


def _base_settings():
    return {
        "theme": "Dark",
        "window_filter": "",
        "recoil": {
            "enabled": False,
            "trigger_keys": [],
            "humanize": False,
            "interval_ms": 10,
            "weapons": [{"strength_y": 5}, {"strength_y": 5}, {"strength_y": 5}],
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
            "slot_keys": [],
            "interval_ms": 100,
            "humanize": False,
        },
        "indicator": {"enabled": True, "position": "below_crosshair"},
        "macros": [],
        "toggles": [],
        "stats": {"enabled": False, "corner": "top_right", "update_rate_hz": 1,
                   "show_cpu_usage": True, "show_cpu_temp": True},
    }


def _make_engine(settings):
    eng = RecoilEngine(settings)
    eng._kbDevice = _FakeDevice()
    eng._msDevice = _FakeDevice()
    return eng


def _key_stroke(code, is_up=False, e0=False):
    flags = 0
    if is_up:
        flags |= interception.KeyFlag.KEY_UP
    if e0:
        flags |= interception.KeyFlag.KEY_E0
    return interception.KeyStroke(code, flags)


def _mouse_stroke(button_flag, data=0):
    return interception.MouseStroke(0, button_flag, data, 0, 0)


_MB4_DOWN = interception.MouseButtonFlag.MOUSE_BUTTON_4_DOWN
_MB4_UP   = interception.MouseButtonFlag.MOUSE_BUTTON_4_UP
_MB5_DOWN = interception.MouseButtonFlag.MOUSE_BUTTON_5_DOWN
_MB5_UP   = interception.MouseButtonFlag.MOUSE_BUTTON_5_UP
_MB_LEFT_DOWN = interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN
_MB_LEFT_UP   = interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_UP
_MB_WHEEL     = interception.MouseButtonFlag.MOUSE_WHEEL

# Scancodes 2/3/4 == "1"/"2"/"3" (see recoil.SCANCODE_NAMES) -- arbitrary,
# just need three distinct non-hotkey, non-protected codes.
_CODE_1 = 2
_CODE_2 = 3
_CODE_3 = 4


class TestMouseSlotKeyDisableForcesDisarm(unittest.TestCase):
    """Exact repro from the bug report: 3 mouse slot keys, slot 1 enabled,
    slots 2/3 disabled."""

    def _engine(self):
        settings = _base_settings()
        settings["rapidfire"]["slot_keys"] = [
            {"type": "mouse", "button": "mouse_x1", "enabled": True},
            {"type": "mouse", "button": "mouse_x2", "enabled": False},
        ]
        return _make_engine(settings)

    def test_disabled_slot_forces_rf_off_after_enabled_arm(self):
        eng = self._engine()
        # Weapon 1: arm via the enabled slot key.
        eng._handleMouseStroke(_mouse_stroke(_MB4_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB4_UP), None)
        self.assertTrue(eng._rfArmed, "enabled slot key must still toggle-arm")

        # Weapon 2: disabled slot key must force RF off, not leave it
        # untouched (the bug: previously _rfArmed stayed True here).
        eng._handleMouseStroke(_mouse_stroke(_MB5_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB5_UP), None)
        self.assertFalse(eng._rfArmed)

    def test_disabled_slot_is_idempotent_not_a_toggle(self):
        """Distinguishes force-off from an accidental toggle: pressing the
        disabled slot key twice in a row must stay OFF both times, not
        flip back ON on the second press."""
        eng = self._engine()
        eng._handleMouseStroke(_mouse_stroke(_MB4_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB4_UP), None)
        self.assertTrue(eng._rfArmed)

        eng._handleMouseStroke(_mouse_stroke(_MB5_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB5_UP), None)
        self.assertFalse(eng._rfArmed)

        eng._handleMouseStroke(_mouse_stroke(_MB5_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB5_UP), None)
        self.assertFalse(eng._rfArmed, "a second press of the same disabled "
                                        "slot key must not toggle back ON")

    def test_disabled_slot_also_selects_the_weapon(self):
        # Weapon-select slots are a separately-indexed list from RF's own
        # slot_keys (see recoil.py's _activeWeapon docstring) -- configured
        # here with the same physical keys as the RF slot_keys above, as a
        # user actually would.
        settings = _base_settings()
        settings["rapidfire"]["slot_keys"] = [
            {"type": "mouse", "button": "mouse_x1", "enabled": True},
            {"type": "mouse", "button": "mouse_x2", "enabled": False},
        ]
        settings["recoil"]["weapons"] = [
            {"type": "mouse", "button": "mouse_x1", "strength_y": 5},
            {"type": "mouse", "button": "mouse_x2", "strength_y": 8},
        ]
        eng = _make_engine(settings)

        eng._handleMouseStroke(_mouse_stroke(_MB5_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB5_UP), None)
        self.assertEqual(eng._activeWeaponIdx, 1, "weapon selection must "
                          "still happen even though the RF slot is disabled")


class TestScrollSlotKeyDisableForcesDisarm(unittest.TestCase):
    def _engine(self):
        settings = _base_settings()
        settings["rapidfire"]["slot_keys"] = [
            {"type": "scroll", "direction": "up", "enabled": True},
            {"type": "scroll", "direction": "down", "enabled": False},
        ]
        return _make_engine(settings)

    def test_disabled_scroll_slot_forces_rf_off(self):
        eng = self._engine()
        eng._handleMouseStroke(_mouse_stroke(_MB_WHEEL, 120), None)   # up: arm
        self.assertTrue(eng._rfArmed)

        eng._handleMouseStroke(_mouse_stroke(_MB_WHEEL, 65536 - 120), None)  # down: disabled
        self.assertFalse(eng._rfArmed)


class TestKeyboardSlotKeyDisableForcesDisarm(unittest.TestCase):
    def _engine(self):
        settings = _base_settings()
        settings["rapidfire"]["slot_keys"] = [
            {"type": "key", "code": _CODE_1, "e0": False, "enabled": True},
            {"type": "key", "code": _CODE_2, "e0": False, "enabled": False},
        ]
        return _make_engine(settings)

    def test_disabled_keyboard_slot_forces_rf_off(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=True), None)
        self.assertTrue(eng._rfArmed)

        eng._handleKeyboardStroke(_key_stroke(_CODE_2, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_2, is_up=True), None)
        self.assertFalse(eng._rfArmed)

    def test_enabled_slot_toggle_behavior_unchanged(self):
        """The enabled-slot path must still alternate on repeated presses --
        this fix must not change that existing, working behavior. (Resets
        the arm-toggle debounce timestamp between presses -- see
        _tryToggleRfArmed_locked/_RF_ARM_DEBOUNCE_SEC -- since that leading-
        edge debounce is unrelated to this fix and would otherwise just
        ignore the second immediate press in a fast unit test.)"""
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=True), None)
        self.assertTrue(eng._rfArmed)

        eng._lastRfArmToggleTime = None
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_1, is_up=True), None)
        self.assertFalse(eng._rfArmed)


class TestDisabledSlotStopsActiveFireImmediately(unittest.TestCase):
    """Mid-fire edge case: switching to a disabled weapon slot while RF is
    actively firing (trigger physically held) must stop new fire cycles
    right away (_rfFireHeld cleared), but must NOT clear _rfSuppressing --
    that flag has to stay True until the real trigger-key release so
    _coreMouseButtonUp's suppress_rf capture still swallows that eventual
    physical up (its matching physical down was swallowed when RF first
    took over; leaking the up unsuppressed would send the game an
    unpaired event)."""

    def _engine(self):
        settings = _base_settings()
        settings["rapidfire"]["slot_keys"] = [
            {"type": "mouse", "button": "mouse_x1", "enabled": True},
            {"type": "mouse", "button": "mouse_x2", "enabled": False},
        ]
        return _make_engine(settings)

    def test_mid_fire_weapon_switch_to_disabled_slot(self):
        eng = self._engine()

        # Arm RF via weapon 1's enabled slot key.
        eng._handleMouseStroke(_mouse_stroke(_MB4_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB4_UP), None)
        self.assertTrue(eng._rfArmed)

        # Physically press the RF trigger -- RF takes over, raw down
        # suppressed, fire state latched.
        suppressed_down = eng._handleMouseStroke(_mouse_stroke(_MB_LEFT_DOWN), None)
        self.assertTrue(suppressed_down)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)

        # Still physically holding the trigger, switch to weapon 2 (disabled
        # slot key) mid-fire.
        eng._handleMouseStroke(_mouse_stroke(_MB5_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB5_UP), None)

        self.assertFalse(eng._rfArmed, "must be force-disarmed")
        self.assertFalse(eng._rfFireHeld, "must stop the fire loop from "
                          "issuing further synthesized clicks immediately")
        self.assertTrue(eng._rfSuppressing, "must NOT be cleared yet -- the "
                         "eventual real trigger release still needs this to "
                         "suppress the matching physical up")

        # Now the user actually releases the trigger button.
        suppressed_up = eng._handleMouseStroke(_mouse_stroke(_MB_LEFT_UP), None)
        self.assertTrue(suppressed_up, "the physical up must still be "
                         "swallowed -- its matching physical down never "
                         "reached the game either")
        self.assertFalse(eng._rfSuppressing, "now cleared by the real "
                          "trigger-release path")
        self.assertFalse(eng._rfFireHeld)


if __name__ == "__main__":
    unittest.main()
