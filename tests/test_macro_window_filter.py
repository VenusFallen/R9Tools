"""
Unit test for the window_filter fix in macro_engine.py.

Unlike the other files in this directory (which are interactive manual
diagnostic scripts requiring admin + physical key/mouse input), this test
is fully automated: it constructs interception stroke objects directly
(no driver I/O) and monkeypatches macro_engine.windowMatchesFilter to
avoid depending on real foreground-window state.

Run with: python -m unittest tests.test_macro_window_filter -v
       or: python -m pytest tests/test_macro_window_filter.py -v
"""
import sys
import os
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interception
import macro_engine


def _key_macro(mode="once"):
    return {
        "enabled": True,
        "mode": mode,
        "trigger": {"type": "key", "code": 30, "e0": False},  # scancode 30 = 'A'
        "actions": [],
        "humanize": False,
    }


class TestMacroWindowFilter(unittest.TestCase):
    def _engine(self, macros, window_filter=""):
        settings = {"macros": macros, "window_filter": window_filter}
        eng = macro_engine.MacroEngine(settings)
        self.addCleanup(eng.stop)
        return eng

    def test_trigger_fires_when_window_matches(self):
        eng = self._engine([_key_macro()], window_filter="game.exe")
        with patch.object(macro_engine, "windowMatchesFilter", return_value=True) as m:
            keyup = interception.KeyStroke(30, interception.KeyFlag.KEY_UP)
            eng.handleStroke(keyup, is_keyboard=True, is_e0=False)
            m.assert_called_once_with("game.exe")
        # macro was queued for playback
        time.sleep(0.05)
        self.assertIsNotNone(eng._activeMacro)

    def test_trigger_suppressed_when_window_does_not_match(self):
        eng = self._engine([_key_macro()], window_filter="game.exe")
        with patch.object(macro_engine, "windowMatchesFilter", return_value=False) as m:
            keyup = interception.KeyStroke(30, interception.KeyFlag.KEY_UP)
            eng.handleStroke(keyup, is_keyboard=True, is_e0=False)
            m.assert_called_once_with("game.exe")
        # macro must NOT have been queued
        self.assertIsNone(eng._activeMacro)

    def test_no_macros_short_circuits_before_window_check(self):
        eng = self._engine([], window_filter="game.exe")
        with patch.object(macro_engine, "windowMatchesFilter") as m:
            keyup = interception.KeyStroke(30, interception.KeyFlag.KEY_UP)
            eng.handleStroke(keyup, is_keyboard=True, is_e0=False)
            m.assert_not_called()

    def test_recording_ignores_window_filter(self):
        """Recording must capture strokes regardless of foreground window —
        only trigger *detection* is scoped by window_filter."""
        eng = self._engine([_key_macro()], window_filter="game.exe")
        eng.startRecording()
        with patch.object(macro_engine, "windowMatchesFilter", return_value=False) as m:
            keydown = interception.KeyStroke(30, 0)
            eng.handleStroke(keydown, is_keyboard=True, is_e0=False)
            m.assert_not_called()  # recording path returns before the filter check
        actions = eng.stopRecording()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["type"], "key_down")
        self.assertEqual(actions[0]["code"], 30)

    def test_empty_window_filter_allows_trigger_without_win32_check(self):
        """An empty window_filter means 'no restriction' — should not even
        need to consult windowMatchesFilter's real implementation to pass."""
        eng = self._engine([_key_macro()], window_filter="")
        with patch.object(macro_engine, "windowMatchesFilter", wraps=macro_engine.windowMatchesFilter) as m:
            keyup = interception.KeyStroke(30, interception.KeyFlag.KEY_UP)
            eng.handleStroke(keyup, is_keyboard=True, is_e0=False)
        time.sleep(0.05)
        self.assertIsNotNone(eng._activeMacro)


if __name__ == "__main__":
    unittest.main()
