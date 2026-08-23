"""
Unit tests for the "hold-to-toggle" feature (settings["toggles"]).

Converts a key/mouse-button's native hold behavior into a toggle: press once
= logically held until pressed again. See recoil.py's
RecoilEngine._lookupToggleTarget / _tryToggleDown / _toggleOwnInput /
_forceReleaseActiveToggles_locked / _toggleStillEnabled_locked for the
implementation, checked as a branch positioned after remap and before
today's normal _coreMouseButtonDown/Up / _updateHeldKeyboard handling in
_handleMouseStroke / _handleKeyboardStroke (mirroring the existing
"first branch wins" exclusivity precedent remap already establishes).

State machine under test:
  - Physical down, sig resolves to an enabled toggle, currently OFF: let the
    real down through unmodified (suppress=False), mark ON, register the
    identity into _held (cross-system feedback for RF/recoil trigger
    detection) WITHOUT re-feeding the macro engine (the raw physical stroke
    is already fed unconditionally by _listenLoop, outside what these tests
    drive directly).
  - Physical down, sig resolves to an enabled toggle, currently ON: suppress
    the physical down, synthesize a release for the SAME identity (reusing
    _sendSynthesized, whose own feedback plumbing removes it from _held and
    feeds the macro engine with the synthesized stroke), mark OFF.
  - Physical up, sig resolves to an enabled toggle: ALWAYS suppressed,
    regardless of current ON/OFF state.
  - Stuck-key cleanup: force-release on stop() (quit), on
    updateSettings(full_reset=True) (profile switch), and on a routine
    updateSettings() where the specific active toggle's entry was
    disabled/removed -- but NOT on an unrelated settings change (a toggle's
    ON state must persist across unrelated panel edits, unlike remap's
    active-remap state which is cleared unconditionally on every call).

These tests drive _handleMouseStroke()/_handleKeyboardStroke()/stop()/
updateSettings() directly with constructed interception KeyStroke/
MouseStroke objects (no real hardware, no Interception() driver instance,
no listen thread). self._kbDevice/self._msDevice are stubbed with a fake
recording device so _sendSynthesized() actually "sends" into a list without
touching the OS.

Run with: python -m unittest tests.test_recoil_toggles -v
       or: python -m pytest tests/test_recoil_toggles.py -v
"""
import sys
import os
import copy
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interception
from recoil import RecoilEngine


class _FakeDevice:
    """Stand-in for the interception device object RecoilEngine caches as
    self._kbDevice/self._msDevice. Records every stroke sent to it instead
    of touching the OS."""
    def __init__(self):
        self.sent = []

    def send(self, stroke):
        self.sent.append(stroke)


class _FakeThread:
    """Stand-in for the RecoilEngine's own background threads so stop() can
    be exercised directly without ever calling start() (which would spin up
    a real listen loop that tries to bring up the Interception driver)."""
    def join(self, timeout=None):
        pass


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
            "enabled": False,
            "mappings": [],
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
        "toggles": [],
        "stats": {"enabled": False, "corner": "top_right", "update_rate_hz": 1,
                   "show_cpu_usage": True, "show_cpu_temp": True},
    }


def _make_engine(settings=None, with_devices=True):
    eng = RecoilEngine(settings or _base_settings())
    if with_devices:
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


_MB_LEFT_DOWN  = interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN
_MB_LEFT_UP    = interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_UP
_MB_RIGHT_DOWN = interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN
_MB_RIGHT_UP   = interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP

# Scancode 32 == "D" (see recoil.SCANCODE_NAMES)
_CODE_D = 32


class TestToggleKeyStateMachineAlternation(unittest.TestCase):
    """Scancode 'D' (32) configured as a toggle."""

    def _engine(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "Sprint", "type": "key", "code": _CODE_D, "e0": False,
             "enabled": True},
        ]
        return _make_engine(settings)

    def test_first_down_not_suppressed_and_marks_on(self):
        eng = self._engine()
        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=False), None)
        self.assertFalse(suppressed, "first physical down must pass through unmodified")
        self.assertIn(("key", _CODE_D, False), eng._toggleActive)

    def test_first_down_registers_own_identity_in_held(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=False), None)
        self.assertIn("D", eng._held)

    def test_up_right_after_first_down_is_suppressed(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=False), None)
        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        self.assertTrue(suppressed, "physical up for a toggle-configured key "
                                     "must never reach the game")
        # Held state must persist across the (suppressed) physical release --
        # this is the entire point of "hold-to-toggle".
        self.assertIn("D", eng._held)
        self.assertIn(("key", _CODE_D, False), eng._toggleActive)

    def test_second_down_suppresses_and_synthesizes_release(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)

        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=False), None)
        self.assertTrue(suppressed, "second physical down must be suppressed")
        self.assertNotIn(("key", _CODE_D, False), eng._toggleActive)
        self.assertNotIn("D", eng._held)

        sent = eng._kbDevice.sent
        self.assertEqual(len(sent), 1, "exactly one synthesized release")
        self.assertEqual(sent[0].code, _CODE_D)
        self.assertTrue(bool(sent[0].flags & interception.KeyFlag.KEY_UP))

    def test_full_on_off_on_cycle(self):
        eng = self._engine()
        # ON
        self.assertFalse(eng._handleKeyboardStroke(_key_stroke(_CODE_D), None))
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        # OFF
        self.assertTrue(eng._handleKeyboardStroke(_key_stroke(_CODE_D), None))
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        # ON again
        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertFalse(suppressed)
        self.assertIn(("key", _CODE_D, False), eng._toggleActive)
        self.assertIn("D", eng._held)


class TestToggleMouseStateMachine(unittest.TestCase):
    """mouse_right configured as a toggle."""

    def _engine(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "mouse", "button": "mouse_right", "enabled": True},
        ]
        return _make_engine(settings)

    def test_first_down_not_suppressed_marks_on_and_held(self):
        eng = self._engine()
        suppressed = eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_DOWN), None)
        self.assertFalse(suppressed)
        self.assertIn(("mouse", "mouse_right"), eng._toggleActive)
        self.assertIn("mouse_right", eng._held)

    def test_up_is_always_suppressed(self):
        eng = self._engine()
        eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_DOWN), None)
        suppressed = eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_UP), None)
        self.assertTrue(suppressed)
        self.assertIn("mouse_right", eng._held)

    def test_second_down_suppresses_and_synthesizes_release(self):
        eng = self._engine()
        eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_DOWN), None)
        eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_UP), None)

        suppressed = eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_DOWN), None)
        self.assertTrue(suppressed)
        self.assertNotIn(("mouse", "mouse_right"), eng._toggleActive)
        self.assertNotIn("mouse_right", eng._held)

        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].button_flags, _MB_RIGHT_UP)


class TestToggleCrossSystemFeedback(unittest.TestCase):
    """RF/recoil trigger detection must see the toggle's identity as held
    for its full ON duration, not just its instantaneous physical press."""

    def _engine(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "key", "code": _CODE_D, "e0": False, "enabled": True},
        ]
        settings["recoil"]["trigger_keys"] = ["D"]
        return _make_engine(settings)

    def test_recoil_isActive_persists_across_physical_release(self):
        eng = self._engine()
        eng._settings["enabled"] = True
        self.assertFalse(eng.isActive)

        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertTrue(eng.isActive)

        # Physical release is suppressed but must NOT drop the trigger hold.
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        self.assertTrue(eng.isActive, "toggle ON must keep recoil active "
                                       "after the user's finger physically lifts")

        # Second press turns it back OFF.
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertFalse(eng.isActive)


class TestToggleMacroFeedNotDoubled(unittest.TestCase):
    """The ON-transition (real down forwarded, no new OS-level event
    synthesized) must NOT re-feed the macro engine -- that raw physical
    stroke is already fed unconditionally by _listenLoop (outside what these
    tests drive). The OFF-transition DOES feed the macro engine, but only
    with the one synthesized release stroke -- mirroring exactly how remap's
    synthesized-TO strokes are already fed into macro trigger detection."""

    def _engine_with_macro_spy(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "key", "code": _CODE_D, "e0": False, "enabled": True},
        ]
        eng = _make_engine(settings)

        from macro_engine import MacroEngine
        macro_eng = MacroEngine(settings)
        eng.setMacroEngine(macro_eng)
        self.addCleanup(macro_eng.stop)

        fed = []
        macro_eng.handleStroke = lambda *a, **kw: fed.append((a, kw))
        return eng, fed

    def test_on_transition_does_not_feed_macro_engine(self):
        eng, fed = self._engine_with_macro_spy()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertEqual(fed, [], "ON-transition must not re-feed the macro "
                                   "engine -- the raw physical down is "
                                   "already fed unconditionally elsewhere")

    def test_off_transition_feeds_macro_engine_exactly_once(self):
        eng, fed = self._engine_with_macro_spy()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        fed.clear()

        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)  # OFF transition
        self.assertEqual(len(fed), 1)
        stroke = fed[0][0][0]
        self.assertEqual(stroke.code, _CODE_D)
        self.assertTrue(bool(stroke.flags & interception.KeyFlag.KEY_UP))


class TestToggleRemapPrecedence(unittest.TestCase):
    """A key configured as both a remap-FROM and a toggle is not a
    supported combination -- remap must silently win (checked first at
    every call site)."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["enabled"] = True
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": _CODE_D, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_left"}},
        ]
        settings["toggles"] = [
            {"name": "", "type": "key", "code": _CODE_D, "e0": False, "enabled": True},
        ]
        return _make_engine(settings)

    def test_remap_wins_toggle_never_triggers(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertEqual(eng._toggleActive, {}, "toggle must never activate "
                                                 "when remap also matches")
        self.assertIn("mouse_left", eng._held)


class TestToggleProtectedHotkeys(unittest.TestCase):
    """Menu Toggle / Quit may never be used as a toggle binding, mirroring
    remap's identical protection."""

    def _engine(self, code, e0):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "key", "code": code, "e0": e0, "enabled": True},
        ]
        return _make_engine(settings)

    def test_overlay_toggle_binding_never_resolves_as_toggle(self):
        eng = self._engine(82, True)  # matches default overlay_toggle bind
        self.assertIsNone(eng._lookupToggleTarget(("key", 82, True)))

    def test_quit_binding_never_resolves_as_toggle(self):
        eng = self._engine(83, True)  # matches default quit bind
        self.assertIsNone(eng._lookupToggleTarget(("key", 83, True)))


class TestToggleWindowFilter(unittest.TestCase):
    def _engine(self):
        settings = _base_settings()
        settings["window_filter"] = "game.exe"
        settings["toggles"] = [
            {"name": "", "type": "key", "code": _CODE_D, "e0": False, "enabled": True},
        ]
        return _make_engine(settings)

    def test_toggle_inert_when_window_does_not_match(self):
        eng = self._engine()
        eng.windowMatchesFilter = lambda f: False
        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertFalse(suppressed)
        self.assertEqual(eng._toggleActive, {})
        self.assertIn("D", eng._held, "falls through to normal (non-toggle) "
                                       "handling when window filter blocks it")


class TestToggleDisabledEntryNeverMatches(unittest.TestCase):
    def test_disabled_entry_is_inert(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "key", "code": _CODE_D, "e0": False, "enabled": False},
        ]
        eng = _make_engine(settings)
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)
        self.assertEqual(eng._toggleActive, {})
        self.assertIn("D", eng._held)  # normal (non-toggle) handling


class TestToggleScrollIsInert(unittest.TestCase):
    """A malformed toggle entry with type == 'scroll' must never crash and
    must never be treated as an active toggle -- scroll is instantaneous,
    toggling doesn't make sense for it."""

    def test_scroll_type_entry_is_a_no_op(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "", "type": "scroll", "direction": "up", "enabled": True},
        ]
        eng = _make_engine(settings)
        # Direct lookup with a scroll sig must return None, not raise.
        self.assertIsNone(eng._lookupToggleTarget(("scroll", "up")))
        # And the scroll branch of _handleMouseStroke must behave exactly
        # as it does with no toggles configured at all.
        suppressed = eng._handleMouseStroke(
            _mouse_stroke(interception.MouseButtonFlag.MOUSE_WHEEL, 120), None)
        self.assertFalse(suppressed)


class TestStuckKeyCleanupOnUpdateSettings(unittest.TestCase):
    def _engine_with_active_toggle(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "Sprint", "type": "key", "code": _CODE_D, "e0": False,
             "enabled": True},
        ]
        eng = _make_engine(settings)
        eng._handleKeyboardStroke(_key_stroke(_CODE_D), None)  # turn ON
        self.assertIn(("key", _CODE_D, False), eng._toggleActive)
        self.assertIn("D", eng._held)
        return eng

    def test_full_reset_force_releases_active_toggle(self):
        eng = self._engine_with_active_toggle()
        updated = copy.deepcopy(eng._fullSettings)

        eng.updateSettings(updated, full_reset=True)

        self.assertEqual(eng._toggleActive, {})
        self.assertNotIn("D", eng._held)
        sent = eng._kbDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].code, _CODE_D)
        self.assertTrue(bool(sent[0].flags & interception.KeyFlag.KEY_UP))

    def test_disabling_the_specific_active_entry_force_releases_it(self):
        eng = self._engine_with_active_toggle()
        updated = copy.deepcopy(eng._fullSettings)
        updated["toggles"][0]["enabled"] = False

        eng.updateSettings(updated)  # full_reset=False (routine path)

        self.assertEqual(eng._toggleActive, {})
        self.assertNotIn("D", eng._held)
        sent = eng._kbDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertTrue(bool(sent[0].flags & interception.KeyFlag.KEY_UP))

    def test_removing_the_specific_active_entry_force_releases_it(self):
        eng = self._engine_with_active_toggle()
        updated = copy.deepcopy(eng._fullSettings)
        updated["toggles"] = []

        eng.updateSettings(updated)

        self.assertEqual(eng._toggleActive, {})
        sent = eng._kbDevice.sent
        self.assertEqual(len(sent), 1)

    def test_stray_physical_up_after_disable_is_swallowed_not_forwarded(self):
        """If the user's finger is still physically down at the instant the
        toggle entry gets disabled, the eventual real release must not slip
        through as a stray extra up-event once the sig no longer resolves
        as a toggle at all."""
        eng = self._engine_with_active_toggle()
        updated = copy.deepcopy(eng._fullSettings)
        updated["toggles"][0]["enabled"] = False
        eng.updateSettings(updated)

        suppressed = eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        self.assertTrue(suppressed, "the one stray physical up must be "
                                     "swallowed via _togglePendingRelease")
        self.assertEqual(eng._togglePendingRelease, set(),
                          "the pending-release entry must be consumed exactly once")

        # A SECOND physical up (e.g. some later unrelated tap) must now be
        # treated as ordinary passthrough, since the sig is fully forgotten.
        suppressed2 = eng._handleKeyboardStroke(_key_stroke(_CODE_D, is_up=True), None)
        self.assertFalse(suppressed2)

    def test_unrelated_settings_change_does_not_release_active_toggle(self):
        """Regression guard: unlike remap's active-remap state (cleared
        unconditionally on every updateSettings() call), a toggle's ON
        state must survive an unrelated settings change -- e.g. nudging
        crosshair color must not cancel toggle-sprint."""
        eng = self._engine_with_active_toggle()
        updated = copy.deepcopy(eng._fullSettings)
        updated["crosshair"]["color"] = "red"

        eng.updateSettings(updated)  # full_reset=False, toggles unchanged

        self.assertIn(("key", _CODE_D, False), eng._toggleActive)
        self.assertIn("D", eng._held)
        self.assertEqual(eng._kbDevice.sent, [], "no release should have "
                                                  "been synthesized")


class TestStuckKeyCleanupOnStop(unittest.TestCase):
    def test_stop_force_releases_active_toggle(self):
        settings = _base_settings()
        settings["toggles"] = [
            {"name": "Crouch", "type": "mouse", "button": "mouse_right",
             "enabled": True},
        ]
        eng = _make_engine(settings)
        eng._applyThread  = _FakeThread()
        eng._listenThread = _FakeThread()
        eng._rfThread     = _FakeThread()

        eng._handleMouseStroke(_mouse_stroke(_MB_RIGHT_DOWN), None)  # turn ON
        self.assertIn(("mouse", "mouse_right"), eng._toggleActive)

        eng.stop()

        self.assertEqual(eng._toggleActive, {})
        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].button_flags, _MB_RIGHT_UP)

    def test_stop_is_a_no_op_when_no_toggle_is_active(self):
        eng = _make_engine(_base_settings())
        eng._applyThread  = _FakeThread()
        eng._listenThread = _FakeThread()
        eng._rfThread     = _FakeThread()

        eng.stop()  # must not raise

        self.assertEqual(eng._toggleActive, {})
        self.assertEqual(eng._kbDevice.sent, [])
        self.assertEqual(eng._msDevice.sent, [])


if __name__ == "__main__":
    unittest.main()
