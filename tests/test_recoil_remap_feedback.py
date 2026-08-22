"""
Unit tests for remap-synthesized feedback into recoil/RF/macro trigger
detection (item 6, mechanism C).

Background: pre-existing behavior was that a remapped physical key's own
identity got added to RecoilEngine._held (and could satisfy RF trigger_keys/
slot_keys as its own signature) EVEN THOUGH its synthesized output — the
actual thing the game or _sendSynthesized sees — was a completely different
input. E.g. remapping "1" -> mouse_left meant "1" itself still registered in
_held, while recoil/RF configured to trigger on mouse_left never saw
anything, since the synthesized mouse_left down/up was injected directly via
kb.send()/ms.send() and never flowed back through any trigger-detection.

The fix (see recoil.py, _lookupRemapTarget / _registerSynthesizedKeyFeedback
/ _registerSynthesizedMouseFeedback / _registerSynthesizedScrollFeedback):
  - A physical stroke whose signature currently resolves to an active remap
    no longer updates _held / RF trigger_keys / RF & weapon slot_keys under
    its OWN identity at all — that path is skipped entirely in favor of the
    remap application.
  - _sendSynthesized(), right after the real kb.send()/ms.send() for the
    remapped-TO input, calls into the same core trigger-detection logic
    (_coreMouseButtonDown/Up, _updateHeldKeyboard +
    _applyKeyboardSlotAndWeaponSelect, _coreScroll) using the TO identity,
    and feeds a synthesized stroke into MacroEngine.handleStroke() too
    (skipped while a recording is in progress, to avoid double-recording).
  - This feedback path never calls _tryRemap/_lookupRemapTarget (no chained
    remaps) and never calls device.send()/kb.send()/ms.send() a second time
    (no double injection into the OS) — it only updates internal engine
    state.

These tests drive _handleMouseStroke()/_handleKeyboardStroke() directly with
constructed interception KeyStroke/MouseStroke objects (no real hardware,
no Interception() driver instance, no listen thread). `inter=None` is
accepted throughout since self._kbDevice/self._msDevice are stubbed with a
fake recording device instead, so _sendSynthesized() actually "sends" (into
a list) without touching the OS.

Run with: python -m unittest tests.test_recoil_remap_feedback -v
       or: python -m pytest tests/test_recoil_remap_feedback.py -v
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
    of touching the OS, so tests can assert on exactly what would have been
    injected."""
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


class TestKeyToMouseFeedback(unittest.TestCase):
    """Remap "1" (scancode 2) -> mouse_left. Recoil trigger_keys = ["mouse_left"]."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 2, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_left"}},
        ]
        settings["recoil"]["trigger_keys"] = ["mouse_left"]
        return _make_engine(settings)

    def test_source_key_own_identity_not_added_to_held(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertNotIn("1", eng._held,
                          "physical source key's own label must not register "
                          "in _held once it resolves to an active remap")

    def test_synthesized_target_identity_added_to_held_on_down(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertIn("mouse_left", eng._held,
                       "remapped-TO identity should register as held, "
                       "mirroring a real physical mouse_left down")

    def test_synthesized_target_removed_from_held_on_up(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertIn("mouse_left", eng._held)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)
        self.assertNotIn("mouse_left", eng._held)
        self.assertNotIn("1", eng._held)

    def test_recoil_isActive_reacts_to_remapped_key_like_real_mouse_left(self):
        eng = self._engine()
        eng._settings["enabled"] = True
        self.assertFalse(eng.isActive)

        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertTrue(eng.isActive,
                         "recoil configured to trigger on mouse_left should "
                         "activate when the remapped source key is pressed")

        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)
        self.assertFalse(eng.isActive)

    def test_actual_synthesized_output_sent_exactly_once_per_edge(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)

        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 2, "exactly one synthesized down + one up")
        self.assertEqual(sent[0].button_flags, _MB_LEFT_DOWN)
        self.assertEqual(sent[1].button_flags, _MB_LEFT_UP)

    def test_physical_stroke_itself_is_suppressed(self):
        eng = self._engine()
        suppressed = eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertTrue(suppressed)


class TestRfTriggerReactsToRemapFeedback(unittest.TestCase):
    """RF configured with trigger_keys = ["mouse_left"]; remap "1" -> mouse_left.
    Pressing "1" should arm RF's suppress/fire state exactly like a real
    mouse_left hold would."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 2, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_left"}},
        ]
        settings["rapidfire"]["enabled"]      = True
        settings["rapidfire"]["trigger_keys"] = ["mouse_left"]
        return _make_engine(settings)

    def test_rf_suppressing_activates_from_remapped_key_press(self):
        eng = self._engine()
        self.assertFalse(eng._rfSuppressing)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertTrue(eng._rfFireHeld)
        self.assertTrue(eng._rfSuppressing)

    def test_rf_state_clears_on_remapped_key_release(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertTrue(eng._rfSuppressing)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)
        self.assertFalse(eng._rfFireHeld)
        self.assertFalse(eng._rfSuppressing)


class TestRfSlotKeyFeedbackForMouseTarget(unittest.TestCase):
    """RF slot_keys arm/disarm toggle configured against mouse_right;
    remap "2" (scancode 3) -> mouse_right. Releasing "2" should toggle
    _rfArmed exactly like releasing a real physical mouse_right would."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 3, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_right"}},
        ]
        settings["rapidfire"]["slot_keys"] = [
            {"type": "mouse", "button": "mouse_right", "enabled": True},
        ]
        return _make_engine(settings)

    def test_slot_key_arm_toggles_on_remapped_release(self):
        eng = self._engine()
        self.assertFalse(eng._rfArmed)
        eng._handleKeyboardStroke(_key_stroke(3, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(3, is_up=True), None)
        self.assertTrue(eng._rfArmed)


class TestMouseToKeyFeedback(unittest.TestCase):
    """Remap mouse_x2 (source) -> a keyboard key ('D', scancode 32) that's
    itself configured in recoil trigger_keys, keyboard-side."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "mouse", "button": "mouse_middle"},
             "to":   {"type": "key", "code": 32, "e0": False}},
        ]
        settings["recoil"]["trigger_keys"] = ["D"]
        return _make_engine(settings)

    def test_mouse_source_not_added_to_held_and_key_target_is(self):
        eng = self._engine()
        eng._handleMouseStroke(
            _mouse_stroke(interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN), None)
        self.assertNotIn("mouse_middle", eng._held)
        self.assertIn("D", eng._held)

    def test_recoil_isActive_from_mouse_remapped_to_key(self):
        eng = self._engine()
        eng._settings["enabled"] = True
        eng._handleMouseStroke(
            _mouse_stroke(interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN), None)
        self.assertTrue(eng.isActive)

        eng._handleMouseStroke(
            _mouse_stroke(interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP), None)
        self.assertFalse(eng.isActive)
        self.assertNotIn("D", eng._held)


class TestScrollTargetFeedbackIsMomentary(unittest.TestCase):
    """Remap key 'G' (scancode 34) -> scroll up. Scroll targets have no
    held state -- RF slot_keys/weapon-select bound to scroll should fire
    once as a momentary trigger check, and nothing should ever land in
    _held for it."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 34, "e0": False},
             "to":   {"type": "scroll", "direction": "up"}},
        ]
        settings["rapidfire"]["slot_keys"] = [
            {"type": "scroll", "direction": "up", "enabled": True},
        ]
        return _make_engine(settings)

    def test_scroll_target_toggles_rf_armed_once_and_not_held(self):
        eng = self._engine()
        self.assertFalse(eng._rfArmed)
        eng._handleKeyboardStroke(_key_stroke(34, is_up=False), None)
        self.assertTrue(eng._rfArmed)
        # No held-state notion of "scroll" exists anywhere in _held.
        self.assertEqual(eng._held, set())

    def test_key_up_of_scroll_source_does_not_toggle_again(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(34, is_up=False), None)
        self.assertTrue(eng._rfArmed)
        eng._handleKeyboardStroke(_key_stroke(34, is_up=True), None)
        # Scroll remap fires once on down only (see _tryRemap); the
        # up-event for the source key must not be treated as "still
        # remapped" (it was a one-shot, not a held remap).
        self.assertTrue(eng._rfArmed)

    def test_scroll_synthesis_sends_one_wheel_event(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(34, is_up=False), None)
        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].button_flags,
                          interception.MouseButtonFlag.MOUSE_WHEEL)


class TestNoRemapChaining(unittest.TestCase):
    """Hard constraint 1: if the remap target is itself configured as
    another remap's FROM, the feedback path must never trigger a second
    synthesized translation."""

    def _engine(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            # "1" -> mouse_left
            {"from": {"type": "key", "code": 2, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_left"}},
            # mouse_left -> mouse_right (would chain if feedback re-entered
            # remap-lookup)
            {"from": {"type": "mouse", "button": "mouse_left"},
             "to":   {"type": "mouse", "button": "mouse_right"}},
        ]
        return _make_engine(settings)

    def test_pressing_source_key_does_not_cascade_to_second_remap(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)

        # Only mouse_left should register as held -- NOT mouse_right, which
        # would only happen if the mouse_left feedback re-entered the
        # remap-lookup pipeline and matched the second mapping.
        self.assertIn("mouse_left", eng._held)
        self.assertNotIn("mouse_right", eng._held)

        # And the OS-level output must be exactly one down event (mouse_left),
        # never a second synthesized mouse_right on top of it.
        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].button_flags, _MB_LEFT_DOWN)

        # _remapActive must only ever contain the ORIGINAL physical sig
        # ("1"), never an entry keyed by the synthesized mouse_left press --
        # confirms the feedback path never called back into _tryRemap.
        self.assertEqual(list(eng._remapActive.keys()), [("key", 2, False)])

    def test_release_also_does_not_cascade(self):
        eng = self._engine()
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)

        self.assertEqual(eng._held, set())
        self.assertEqual(eng._remapActive, {})

        sent = eng._msDevice.sent
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].button_flags, _MB_LEFT_DOWN)
        self.assertEqual(sent[1].button_flags, _MB_LEFT_UP)


class TestNoDoubleSendToOS(unittest.TestCase):
    """Hard constraint 2: the feedback path must never call
    device.send()/kb.send()/ms.send() a second time for the same event."""

    def test_key_to_key_remap_sends_exactly_two_strokes_for_a_full_press(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 2, "e0": False},
             "to":   {"type": "key", "code": 48, "e0": False}},
        ]
        eng = _make_engine(settings)

        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)

        sent = eng._kbDevice.sent
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].code, 48)
        self.assertFalse(bool(sent[0].flags & interception.KeyFlag.KEY_UP))
        self.assertEqual(sent[1].code, 48)
        self.assertTrue(bool(sent[1].flags & interception.KeyFlag.KEY_UP))


class TestMacroEngineSeesSynthesizedIdentity(unittest.TestCase):
    """macro_engine.handleStroke() should see the synthesized (remapped-TO)
    identity, not the physical source, for its own trigger detection."""

    def _engine_with_macro(self):
        settings = _base_settings()
        settings["remapper"]["mappings"] = [
            {"from": {"type": "key", "code": 2, "e0": False},
             "to":   {"type": "mouse", "button": "mouse_left"}},
        ]
        settings["macros"] = [{
            "enabled": True,
            "trigger": {"type": "mouse", "button": "mouse_left"},
            "mode": "once",
            "actions": [],
        }]
        eng = _make_engine(settings)

        from macro_engine import MacroEngine
        macro_eng = MacroEngine(settings)
        eng.setMacroEngine(macro_eng)
        self.addCleanup(macro_eng.stop)
        return eng, macro_eng

    def test_macro_trigger_fires_from_remapped_key(self):
        eng, macro_eng = self._engine_with_macro()

        fired = []
        orig_queue = macro_eng._queueMacro
        def spy(macro):
            fired.append(macro)
            orig_queue(macro)
        macro_eng._queueMacro = spy

        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)

        self.assertEqual(len(fired), 1,
                          "macro trigger (mode='once', fires on up) "
                          "configured for mouse_left should fire from the "
                          "remapped key's synthesized mouse_left up-event")

    def test_macro_engine_not_fed_synthesized_stroke_while_recording(self):
        eng, macro_eng = self._engine_with_macro()
        macro_eng.startRecording()

        recorded_before = len(macro_eng._recordBuffer)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)

        # The physical source key-down IS recorded (unchanged pre-existing
        # behavior -- that call lives in _listenLoop, not exercised by this
        # unit test which calls _handleKeyboardStroke directly), but the
        # synthesized mouse_left feedback must NOT also be recorded.
        actions = macro_eng.stopRecording()
        mouse_actions = [a for a in actions if a.get("type") in
                         ("mouse_down", "mouse_up", "mouse_click")]
        self.assertEqual(mouse_actions, [],
                          "synthesized remap feedback must not be recorded "
                          "as a macro action")


class TestPhysicalTriggerKeyStillWorksUnremapped(unittest.TestCase):
    """Sanity/regression: an ordinary, non-remapped physical mouse_left
    press must still register itself normally -- this whole mechanism must
    not break the baseline non-remap case."""

    def test_unremapped_mouse_left_still_registers_under_its_own_identity(self):
        settings = _base_settings()
        settings["recoil"]["trigger_keys"] = ["mouse_left"]
        eng = _make_engine(settings)

        eng._settings["enabled"] = True
        eng._handleMouseStroke(_mouse_stroke(_MB_LEFT_DOWN), None)
        self.assertIn("mouse_left", eng._held)
        self.assertTrue(eng.isActive)

        eng._handleMouseStroke(_mouse_stroke(_MB_LEFT_UP), None)
        self.assertNotIn("mouse_left", eng._held)
        self.assertFalse(eng.isActive)

    def test_unremapped_key_still_registers_under_its_own_identity(self):
        settings = _base_settings()
        eng = _make_engine(settings)

        eng._handleKeyboardStroke(_key_stroke(2, is_up=False), None)
        self.assertIn("1", eng._held)
        eng._handleKeyboardStroke(_key_stroke(2, is_up=True), None)
        self.assertNotIn("1", eng._held)


if __name__ == "__main__":
    unittest.main()
