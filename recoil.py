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

# Scancode -> display name for keyboard keys
SCANCODE_NAMES = {
    1: "ESC",
    59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5",
    64: "F6", 65: "F7", 66: "F8", 67: "F9", 68: "F10",
    87: "F11", 88: "F12",
    57: "SPACE", 28: "ENTER", 14: "BACKSPACE",
    29: "LCTRL", 56: "LALT", 42: "LSHIFT", 54: "RSHIFT",
    58: "CAPSLOCK",
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T",
    21: "Y", 22: "U", 23: "I", 24: "O", 25: "P",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G",
    35: "H", 36: "J", 37: "K", 38: "L",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B",
    49: "N", 50: "M",
}


def scancodeLabel(code: int) -> str:
    return SCANCODE_NAMES.get(code, f"SC{code}")


class RecoilEngine:
    def __init__(self, settings: dict):
        self._settings = settings["recoil"]
        self._running = False
        self._lock = threading.Lock()
        self._held: set = set()
        self._toggleCallback = None

        self._overlayCallback = None

        self._applyThread = threading.Thread(target=self._applyLoop, daemon=True)
        self._listenThread = threading.Thread(target=self._listenLoop, daemon=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setToggleCallback(self, cb):
        self._toggleCallback = cb

    def setOverlayCallback(self, cb):
        self._overlayCallback = cb

    def start(self):
        self._running = True
        self._applyThread.start()
        self._listenThread.start()

    def stop(self):
        self._running = False

    def updateSettings(self, settings: dict):
        with self._lock:
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
                interval = self._settings["interval_ms"] / 1000.0

            if enabled and self.isActive:
                interception.move_relative(0, sy)

            time.sleep(interval)

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
        isKeyUp  = bool(stroke.flags & interception.KeyFlag.KEY_UP)
        isE0     = bool(stroke.flags & interception.KeyFlag.KEY_E0)

        if isKeyUp:
            # INSERT = scancode 82 + E0 extended flag (distinguishes from Numpad 0)
            if stroke.code == 82 and isE0:
                if self._overlayCallback:
                    self._overlayCallback()
                return

            # Toggle fires on key release — compare by scancode int
            with self._lock:
                toggleCode = self._settings.get("toggle_key", 68)
            if stroke.code == toggleCode:
                with self._lock:
                    self._settings["enabled"] = not self._settings["enabled"]
                    newState = self._settings["enabled"]
                if self._toggleCallback:
                    self._toggleCallback(newState)
                return
