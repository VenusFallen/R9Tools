"""
Base class shared by all imgui panel UI implementations.

Each panel's draw() is called once per frame when the panel is the active tab.
Panels must be stateless with respect to imgui — they rebuild their UI every
frame from self._settings.

Capture helpers manage background-thread key capture with a threading.Event
so the main render thread can poll for completion without blocking.
"""
import threading
import time
import interception as _ic

from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel
from interception_bringup import bringUpInterception

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}


# ---------------------------------------------------------------------------
# Label helpers (mirror theme.py logic)
# ---------------------------------------------------------------------------

def combo_label(keys: list) -> str:
    parts = []
    for k in keys:
        parts.append(_MOUSE_DISPLAY.get(k, k.upper()))
    return " + ".join(parts) if parts else "None"


def input_label(inp: dict) -> str:
    t = inp.get("type", "key")
    if t == "mouse":
        return _MOUSE_DISPLAY.get(inp.get("button", ""), inp.get("button", "?"))
    if t == "scroll":
        return "Wheel Up" if inp.get("direction") == "up" else "Wheel Down"
    code = inp.get("code", 0)
    if not code:
        return "(unbound)"
    return scancodeLabel(code, inp.get("e0", False))


def binding_label(binding: dict) -> str:
    t = binding.get("type")
    if t == "mouse":
        return _MOUSE_DISPLAY.get(binding.get("button", ""), binding.get("button", "?"))
    if t == "scroll":
        return "Wheel Up" if binding.get("direction") == "up" else "Wheel Down"
    code = binding.get("code", 0)
    if not code:
        return "(unbound)"
    return scancodeLabel(code, binding.get("e0", False))


# ---------------------------------------------------------------------------
# Base panel
# ---------------------------------------------------------------------------

class UIPanel:
    """Base for all imgui panel UI objects."""

    right_anchor: bool = False

    def draw(self):
        raise NotImplementedError

    def reload(self, settings: dict):
        """Override to reset local UI state when a profile is loaded."""
        self._settings = settings


# ---------------------------------------------------------------------------
# Capture helper (replaces KeybindButton / capture thread pattern)
# ---------------------------------------------------------------------------

class CaptureHelper:
    """
    Manages a single background interception capture session.
    Call start() to begin; poll is_done() each frame; call take() to retrieve result.
    """

    # How long a "Capture failed" state should stay visible via
    # failed_active()/should_retain() after a driver bring-up failure,
    # before the panel is expected to drop this helper and go back to
    # showing the normal (unbound/bound) label.
    _FAILURE_DISPLAY_SECS = 1.8

    def __init__(self, keyboard_only: bool = True):
        self._keyboard_only = keyboard_only
        self._capturing     = False
        self._result: dict | None = None
        self._event         = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_suspend    = None
        # Set when the capture thread couldn't stand up the interception
        # driver context at all — distinct from is_done()/take() returning
        # a falsy result, which callers otherwise treat as a silent no-op.
        self._failed         = False
        self._failed_at      = 0.0

    @property
    def capturing(self) -> bool:
        return self._capturing

    @property
    def failed(self) -> bool:
        """True if the most recent capture attempt failed to bring up the
        interception driver context (persists until the next start())."""
        return self._failed

    def failed_active(self) -> bool:
        """True while a failure should still be shown to the user."""
        return self._failed and (time.time() - self._failed_at) < self._FAILURE_DISPLAY_SECS

    def should_retain(self) -> bool:
        """True if the panel should keep holding onto this helper instead
        of discarding it (still capturing, still displaying a failure, or a
        completed result is sitting unconsumed via is_done()/take()).

        Without the is_done() check, a helper whose capture just finished
        successfully would be pruned by panels' per-frame cleanup before
        the same draw() call ever reaches its is_done()/take() consumption
        step — _run()'s finally block clears _capturing (and sets the done
        event) together, so by the time should_retain() is False on
        _capturing alone, the result is already sitting there unread. Once
        take() runs it clears the done event, so a consumed (or never
        started) helper still naturally stops being retained here."""
        return self._capturing or self.failed_active() or self.is_done()

    def is_done(self) -> bool:
        """True if a capture completed since last take()."""
        return self._event.is_set()

    def take(self) -> dict | None:
        """Retrieve result and clear done flag. Does not clear failed() —
        that's cleared by the next start() — so panels can still show a
        failure message for a bit via failed_active()/should_retain()."""
        self._event.clear()
        r = self._result
        self._result = None
        return r

    def start(self, on_suspend=None):
        if self._capturing:
            return
        self._capturing = True
        self._result    = None
        self._failed    = False
        self._event.clear()
        if on_suspend:
            on_suspend(True)
        self._on_suspend = on_suspend
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            def _configure(i):
                i.set_filter(i.is_keyboard, _ic.FilterKeyFlag.FILTER_KEY_ALL)
                if not self._keyboard_only:
                    i.set_filter(i.is_mouse, _ic.FilterMouseButtonFlag.FILTER_MOUSE_ALL)

            inter = bringUpInterception(
                _configure,
                should_continue=lambda: self._capturing,
                context="imgui-capture",
            )
            if inter is None:
                self._failed    = True
                self._failed_at = time.time()
                return

            while self._capturing:
                idx = inter.await_input(100)
                if idx is None:
                    continue
                if idx >= len(inter._devices):
                    continue
                device = inter._devices[idx]
                stroke = device.receive()
                if stroke is None:
                    continue
                device.send(stroke)

                if isinstance(stroke, _ic.KeyStroke):
                    if stroke.flags & _ic.KeyFlag.KEY_UP:
                        self._result = {
                            "code": stroke.code,
                            "e0":   bool(stroke.flags & _ic.KeyFlag.KEY_E0),
                        }
                        break
                elif not self._keyboard_only and isinstance(stroke, _ic.MouseStroke):
                    if stroke.button_flags & _SCROLL_WHEEL_FLAG:
                        delta = stroke.button_data
                        if delta > 32767:
                            delta -= 65536
                        self._result = {
                            "type":      "scroll",
                            "direction": "up" if delta > 0 else "down",
                        }
                        break
                    for name, (down_flag, up_flag) in MOUSE_BUTTON_FLAGS.items():
                        if stroke.button_flags & up_flag:
                            self._result = {"type": "mouse", "button": name}
                            break
                    if self._result:
                        break
        finally:
            self._capturing = False
            if self._on_suspend:
                self._on_suspend(False)
            self._event.set()
