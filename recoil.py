import threading
import time
import interception

# Mouse button down/up flag pairs
MOUSE_BUTTON_FLAGS = {
    "mouse_left":   (interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_UP),
    "mouse_right":  (interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP),
    "mouse_middle": (interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP),
}

# Scancode -> display name for standard keyboard keys
SCANCODE_NAMES = {
    1: "ESC",
    59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5",
    64: "F6", 65: "F7", 66: "F8", 67: "F9", 68: "F10",
    87: "F11", 88: "F12",
    # Number row
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
    7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    12: "-", 13: "=",
    # Whitespace / editing
    15: "TAB", 57: "SPACE", 28: "ENTER", 14: "BACKSPACE",
    # Modifiers
    29: "LCTRL", 56: "LALT", 42: "LSHIFT", 54: "RSHIFT",
    58: "CAPSLOCK",
    # Letters
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T",
    21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G",
    35: "H", 36: "J", 37: "K", 38: "L",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B",
    49: "N", 50: "M",
    # Punctuation
    26: "[", 27: "]", 43: "\\",
    39: ";", 40: "'", 41: "`",
    51: ",", 52: ".", 53: "/",
    # Numpad (no E0 flag)
    69: "NUMLOCK", 70: "SCRLK",
    71: "NUM7", 72: "NUM8", 73: "NUM9", 74: "NUM-",
    75: "NUM4", 76: "NUM5", 77: "NUM6", 78: "NUM+",
    79: "NUM1", 80: "NUM2", 81: "NUM3",
    82: "NUM0", 83: "NUM.",
}

# E0-extended keys that share a scancode with non-extended keys
SCANCODE_NAMES_E0 = {
    28: "NUM ENTER",
    29: "RCTRL",
    56: "RALT",
    71: "HOME",
    72: "UP",
    73: "PGUP",
    75: "LEFT",
    77: "RIGHT",
    79: "END",
    80: "DOWN",
    81: "PGDN",
    82: "INSERT",
    83: "DELETE",
}


def scancodeLabel(code: int, e0: bool = False) -> str:
    if e0:
        return SCANCODE_NAMES_E0.get(code, f"SC{code}e0")
    return SCANCODE_NAMES.get(code, f"SC{code}")


class RecoilEngine:
    def __init__(self, settings: dict):
        self._fullSettings = settings
        self._settings = settings["recoil"]
        self._running = False
        self._lock = threading.Lock()
        self._held: set = set()
        self._toggleCallback      = None
        self._overlayCallback     = None
        self._strengthCallback    = None
        self._hotkeysSuspended    = False
        self._strengthHoldEvents: dict = {}   # "down"/"up" → threading.Event

        self._applyThread = threading.Thread(target=self._applyLoop, daemon=True)
        self._listenThread = threading.Thread(target=self._listenLoop, daemon=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setToggleCallback(self, cb):
        self._toggleCallback = cb

    def setOverlayCallback(self, cb):
        self._overlayCallback = cb

    def setStrengthCallback(self, cb):
        self._strengthCallback = cb

    def setSuspendHotkeys(self, suspended: bool):
        with self._lock:
            self._hotkeysSuspended = suspended

    def start(self):
        self._running = True
        self._applyThread.start()
        self._listenThread.start()

    def stop(self):
        self._running = False

    def updateSettings(self, settings: dict):
        with self._lock:
            self._fullSettings = settings
            self._settings = settings["recoil"]
            self._held.clear()

    @property
    def isActive(self) -> bool:
        with self._lock:
            combo = set(self._settings["trigger_keys"])
            return combo.issubset(self._held)

    # ------------------------------------------------------------------
    # Apply loop
    # ------------------------------------------------------------------

    def _applyLoop(self):
        while self._running:
            with self._lock:
                enabled = self._settings["enabled"]
                sy = self._settings["strength_y"]

            if enabled and self.isActive:
                interception.move_relative(0, sy)

            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Listen loop
    # ------------------------------------------------------------------

    def _listenLoop(self):
        inter = interception.Interception()
        inter.set_filter(
            inter.is_mouse,
            interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL
        )
        inter.set_filter(
            inter.is_keyboard,
            interception.FilterKeyFlag.FILTER_KEY_ALL
        )

        while self._running:
            deviceIdx = inter.await_input(100)
            if deviceIdx is None:
                continue

            device = inter._devices[deviceIdx]
            stroke = device.receive()
            if stroke is None:
                continue

            if isinstance(stroke, interception.MouseStroke):
                self._handleMouseStroke(stroke)
            elif isinstance(stroke, interception.KeyStroke):
                self._handleKeyboardStroke(stroke)

            device.send(stroke)

    def _handleMouseStroke(self, stroke):
        for key, (downFlag, upFlag) in MOUSE_BUTTON_FLAGS.items():
            if stroke.button_flags & downFlag:
                with self._lock:
                    self._held.add(key)
            elif stroke.button_flags & upFlag:
                with self._lock:
                    self._held.discard(key)

    def _handleKeyboardStroke(self, stroke):
        isKeyUp = bool(stroke.flags & interception.KeyFlag.KEY_UP)
        isE0    = bool(stroke.flags & interception.KeyFlag.KEY_E0)

        with self._lock:
            if self._hotkeysSuspended:
                return
            hotkeys      = self._fullSettings.get("hotkeys", {})
            overlayBind  = hotkeys.get("overlay_toggle",       {"code": 82, "e0": True})
            recoilBind   = hotkeys.get("recoil_toggle",        {"code": 68, "e0": False})
            strengthDown = hotkeys.get("recoil_strength_down", {"code": 26, "e0": False})
            strengthUp   = hotkeys.get("recoil_strength_up",   {"code": 27, "e0": False})

        if not isKeyUp:
            # KEY_DOWN: fire immediately and start hold thread (ignore OS key-repeat)
            if stroke.code == strengthDown["code"] and isE0 == strengthDown["e0"]:
                if "down" not in self._strengthHoldEvents:
                    self._applyStrength(-1)
                    evt = threading.Event()
                    self._strengthHoldEvents["down"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(-1, evt), daemon=True).start()
                return
            if stroke.code == strengthUp["code"] and isE0 == strengthUp["e0"]:
                if "up" not in self._strengthHoldEvents:
                    self._applyStrength(1)
                    evt = threading.Event()
                    self._strengthHoldEvents["up"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(1, evt), daemon=True).start()
                return
            return  # ignore all other KEY_DOWN events

        # KEY_UP: cancel hold threads for strength keys
        if stroke.code == strengthDown["code"] and isE0 == strengthDown["e0"]:
            evt = self._strengthHoldEvents.pop("down", None)
            if evt:
                evt.set()
            return
        if stroke.code == strengthUp["code"] and isE0 == strengthUp["e0"]:
            evt = self._strengthHoldEvents.pop("up", None)
            if evt:
                evt.set()
            return

        if stroke.code == overlayBind["code"] and isE0 == overlayBind["e0"]:
            if self._overlayCallback:
                self._overlayCallback()
            return

        if stroke.code == recoilBind["code"] and isE0 == recoilBind["e0"]:
            with self._lock:
                self._settings["enabled"] = not self._settings["enabled"]
                newState = self._settings["enabled"]
            if self._toggleCallback:
                self._toggleCallback(newState)
            return

    def _applyStrength(self, direction: int):
        with self._lock:
            self._settings["strength_y"] = max(1, min(30, self._settings["strength_y"] + direction))
            newStrength = self._settings["strength_y"]
        if self._strengthCallback:
            self._strengthCallback(newStrength)

    def _strengthHoldLoop(self, direction: int, stop_event: threading.Event):
        # Wait 1s initial delay; returns True if key released early
        if stop_event.wait(0.5):
            return
        # Repeat every 0.25s until key is released
        while not stop_event.wait(0.25):
            self._applyStrength(direction)
