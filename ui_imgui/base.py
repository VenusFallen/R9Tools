"""
Base class shared by all imgui panel UI implementations.

Each panel's draw() is called once per frame when the panel is the active tab.
Panels must be stateless with respect to imgui — they rebuild their UI every
frame from self._settings.

Capture helpers manage background-thread key capture with a threading.Event
so the main render thread can poll for completion without blocking.
"""
import threading
import interception as _ic

from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel

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

    def __init__(self, keyboard_only: bool = True):
        self._keyboard_only = keyboard_only
        self._capturing     = False
        self._result: dict | None = None
        self._event         = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def capturing(self) -> bool:
        return self._capturing

    def is_done(self) -> bool:
        """True if a capture completed since last take()."""
        return self._event.is_set()

    def take(self) -> dict | None:
        """Retrieve result and clear done flag."""
        self._event.clear()
        r = self._result
        self._result = None
        return r

    def start(self, on_suspend=None):
        if self._capturing:
            return
        self._capturing = True
        self._result    = None
        self._event.clear()
        if on_suspend:
            on_suspend(True)
        self._on_suspend = on_suspend
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        inter = _ic.Interception()
        inter.set_filter(inter.is_keyboard, _ic.FilterKeyFlag.FILTER_KEY_ALL)
        if not self._keyboard_only:
            inter.set_filter(inter.is_mouse, _ic.FilterMouseButtonFlag.FILTER_MOUSE_ALL)

        try:
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
