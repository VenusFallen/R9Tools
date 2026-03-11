import threading
import time
import random
import interception

try:
    import win32gui
    import win32process
    import psutil
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

# ---------------------------------------------------------------------------
# Extended mouse button flags (X1/X2 may not exist in all builds)
# ---------------------------------------------------------------------------
try:
    _MB4_DOWN = int(interception.MouseButtonFlag.MOUSE_BUTTON_4_DOWN)
    _MB4_UP   = int(interception.MouseButtonFlag.MOUSE_BUTTON_4_UP)
    _MB5_DOWN = int(interception.MouseButtonFlag.MOUSE_BUTTON_5_DOWN)
    _MB5_UP   = int(interception.MouseButtonFlag.MOUSE_BUTTON_5_UP)
except AttributeError:
    _MB4_DOWN, _MB4_UP = 0x0040, 0x0080
    _MB5_DOWN, _MB5_UP = 0x0100, 0x0200

try:
    _SCROLL_WHEEL_FLAG = int(interception.MouseButtonFlag.MOUSE_WHEEL)
except AttributeError:
    _SCROLL_WHEEL_FLAG = 0x0400

# Mouse button down/up flag pairs (includes X1/X2 for remapper)
MOUSE_BUTTON_FLAGS = {
    "mouse_left":   (interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_UP),
    "mouse_right":  (interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP),
    "mouse_middle": (interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP),
    "mouse_x1":    (_MB4_DOWN, _MB4_UP),
    "mouse_x2":    (_MB5_DOWN, _MB5_UP),
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
        self._quitCallback        = None
        self._hotkeysSuspended    = False
        self._strengthHoldEvents: dict = {}   # "down"/"up" → threading.Event
        self._remapActive: dict = {}           # sig tuple → to_input dict
        self._kbDevice = None                  # tracked from listen loop for synthesis
        self._msDevice = None                  # tracked from listen loop for synthesis
        self._xDrift   = 0.0                   # Brownian X drift accumulator

        self._rfArmed        = False   # True when weapon slot key has been toggled on
        self._rfFireHeld     = False   # True while all RF trigger keys are physically held
        self._rfSuppressing  = False   # True when we have suppressed the fire trigger
        self._activeWeaponIdx = 0      # index into recoil.weapons list
        self._macroEngine    = None    # set via setMacroEngine() after construction

        self._applyThread = threading.Thread(target=self._applyLoop, daemon=True)
        self._listenThread = threading.Thread(target=self._listenLoop, daemon=True)
        self._rfThread     = threading.Thread(target=self._rfFireLoop, daemon=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setToggleCallback(self, cb):
        self._toggleCallback = cb

    def setOverlayCallback(self, cb):
        self._overlayCallback = cb

    def setStrengthCallback(self, cb):
        self._strengthCallback = cb

    def setQuitCallback(self, cb):
        self._quitCallback = cb

    def setMacroEngine(self, engine):
        self._macroEngine = engine

    def setSuspendHotkeys(self, suspended: bool):
        with self._lock:
            self._hotkeysSuspended = suspended

    def start(self):
        self._running = True
        self._applyThread.start()
        self._listenThread.start()
        self._rfThread.start()

    def stop(self):
        self._running = False

    def updateSettings(self, settings: dict):
        with self._lock:
            active_remaps = dict(self._remapActive)
            # Stop any running strength hold threads before swapping settings
            for evt in self._strengthHoldEvents.values():
                evt.set()
            self._strengthHoldEvents.clear()
            self._fullSettings   = settings
            self._settings       = settings["recoil"]
            self._held.clear()
            self._remapActive.clear()
            self._rfArmed         = False
            self._rfFireHeld      = False
            self._rfSuppressing   = False
            self._activeWeaponIdx = 0

        # Release any remapped outputs that were held at settings-change time
        for to_input in active_remaps.values():
            if to_input.get("type") in ("key", "mouse"):
                self._sendSynthesized(to_input, True, None)

    @property
    def isActive(self) -> bool:
        with self._lock:
            combo = set(self._settings["trigger_keys"])
            return combo.issubset(self._held)

    @property
    def rfArmed(self) -> bool:
        with self._lock:
            return self._rfArmed

    @property
    def rfFiring(self) -> bool:
        with self._lock:
            return self._rfFireHeld and self._rfSuppressing

    def _rfShouldActivate_locked(self) -> bool:
        """Check RF conditions. Must be called with self._lock held."""
        rf = self._fullSettings.get("rapidfire", {})
        return rf.get("enabled", False) and self._rfArmed

    def _activeWeapon(self) -> dict:
        """Return active weapon dict. Must be called with self._lock held."""
        weapons = self._settings.get("weapons", [])
        if weapons and self._activeWeaponIdx < len(weapons):
            return weapons[self._activeWeaponIdx]
        return {"strength_y": 5}

    @property
    def activeWeaponIdx(self) -> int:
        with self._lock:
            return self._activeWeaponIdx

    # ------------------------------------------------------------------
    # Apply loop
    # ------------------------------------------------------------------

    def _applyLoop(self):
        while self._running:
            with self._lock:
                enabled       = self._settings["enabled"]
                sy            = self._activeWeapon().get("strength_y", 5)
                humanize      = self._settings.get("humanize", False)
                window_filter = self._fullSettings.get("window_filter", "")

            if enabled and self.isActive and self.windowMatchesFilter(window_filter):
                if humanize:
                    self._applyHumanized(sy)
                else:
                    interception.move_relative(0, sy)
                    time.sleep(0.05)
            else:
                self._xDrift = 0.0  # reset drift when not actively pulling
                time.sleep(0.05)

    def _applyHumanized(self, sy: int):
        # ~3% chance to skip this tick entirely (simulate human inconsistency)
        if random.random() < 0.03:
            time.sleep(random.uniform(0.042, 0.058))
            return

        # Ornstein-Uhlenbeck X drift: mean-reverting random walk
        self._xDrift = self._xDrift * 0.75 + random.gauss(0, 0.5)
        self._xDrift = max(-2.0, min(2.0, self._xDrift))
        x_move = round(self._xDrift)

        # Split sy into 3 sub-steps with Gaussian noise; last step corrects remainder
        step1 = max(0, round(random.gauss(sy / 3, sy * 0.07)))
        step2 = max(0, round(random.gauss(sy / 3, sy * 0.07)))
        step3 = max(0, sy - step1 - step2)

        step_sleep = random.uniform(0.042, 0.058) / 3

        interception.move_relative(x_move, step1)
        time.sleep(step_sleep)
        interception.move_relative(0, step2)
        time.sleep(step_sleep)
        interception.move_relative(0, step3)
        time.sleep(step_sleep)

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

            if deviceIdx >= len(inter._devices):
                continue
            device = inter._devices[deviceIdx]
            stroke = device.receive()
            if stroke is None:
                continue

            # Track devices for use in synthesis
            is_kb = inter.is_keyboard(deviceIdx)
            if is_kb:
                self._kbDevice = device
            elif inter.is_mouse(deviceIdx):
                self._msDevice = device

            # Keep macro engine device refs current
            if self._macroEngine:
                self._macroEngine.setDevices(self._kbDevice, self._msDevice)

            is_e0 = False
            if isinstance(stroke, interception.KeyStroke):
                is_e0 = bool(stroke.flags & interception.KeyFlag.KEY_E0)

            suppress = False
            if isinstance(stroke, interception.MouseStroke):
                suppress = self._handleMouseStroke(stroke, inter)
            elif isinstance(stroke, interception.KeyStroke):
                suppress = self._handleKeyboardStroke(stroke, inter)

            # Macro trigger detection — never suppresses, fires as side effect
            if self._macroEngine:
                self._macroEngine.handleStroke(stroke, is_kb, is_e0)

            if not suppress:
                device.send(stroke)

    def _handleMouseStroke(self, stroke, inter) -> bool:
        # Track held buttons for recoil trigger detection
        for key, (downFlag, upFlag) in MOUSE_BUTTON_FLAGS.items():
            if stroke.button_flags & downFlag:
                is_rf_trig    = False
                should_activate = False
                wf            = ""
                with self._lock:
                    self._held.add(key)
                    rf           = self._fullSettings.get("rapidfire", {})
                    trigger_keys = set(rf.get("trigger_keys", []))
                    wf           = self._fullSettings.get("window_filter", "")

                    if key in trigger_keys:
                        is_rf_trig = True
                        if self._rfSuppressing:
                            # Already suppressing — suppress re-press of trigger key
                            return True
                        if trigger_keys.issubset(self._held):
                            self._rfFireHeld = True
                            should_activate  = self._rfShouldActivate_locked()

                # windowMatchesFilter uses Win32 APIs — call outside the lock
                if is_rf_trig and should_activate and self.windowMatchesFilter(wf):
                    with self._lock:
                        self._rfSuppressing = True
                    return True
                return self._tryRemap(("mouse", key), False, inter)

            elif stroke.button_flags & upFlag:
                suppress_rf = False
                with self._lock:
                    self._held.discard(key)
                    rf           = self._fullSettings.get("rapidfire", {})
                    trigger_keys = set(rf.get("trigger_keys", []))
                    slot_keys    = list(rf.get("slot_keys", []))
                    weaponSlots  = list(self._settings.get("weapons", []))
                    if key in trigger_keys:
                        self._rfFireHeld = False
                        suppress_rf      = self._rfSuppressing
                        self._rfSuppressing = False
                    # Mouse slot keys: set RF armed state
                    for sk in slot_keys:
                        if sk.get("type") == "mouse" and sk.get("button") == key:
                            self._rfArmed = sk.get("enabled", True)
                            break
                    # Mouse weapon slots: select active weapon
                    for i, w in enumerate(weaponSlots):
                        if w.get("type") == "mouse" and w.get("button") == key:
                            self._activeWeaponIdx = i
                            break
                if suppress_rf:
                    return True
                return self._tryRemap(("mouse", key), True, inter)

        # Scroll wheel
        if stroke.button_flags & _SCROLL_WHEEL_FLAG:
            delta = stroke.button_data
            if delta > 32767:   # interpret uint16 as signed
                delta -= 65536
            direction = "up" if delta > 0 else "down"
            with self._lock:
                rf          = self._fullSettings.get("rapidfire", {})
                slot_keys   = list(rf.get("slot_keys", []))
                weaponSlots = list(self._settings.get("weapons", []))
                # Scroll slot keys: set RF armed state
                for sk in slot_keys:
                    if sk.get("type") == "scroll" and sk.get("direction") == direction:
                        self._rfArmed = sk.get("enabled", True)
                        break
                # Scroll weapon slots: select active weapon
                for i, w in enumerate(weaponSlots):
                    if w.get("type") == "scroll" and w.get("direction") == direction:
                        self._activeWeaponIdx = i
                        break
            return self._tryRemap(("scroll", direction), None, inter)

        return False

    def _handleKeyboardStroke(self, stroke, inter) -> bool:
        isKeyUp = bool(stroke.flags & interception.KeyFlag.KEY_UP)
        isE0    = bool(stroke.flags & interception.KeyFlag.KEY_E0)

        with self._lock:
            if self._hotkeysSuspended:
                return False
            hotkeys      = self._fullSettings.get("hotkeys", {})
            overlayBind  = hotkeys.get("overlay_toggle",       {"code": 82, "e0": True})
            recoilBind   = hotkeys.get("recoil_toggle",        {"code": 68, "e0": False})
            strengthDown = hotkeys.get("recoil_strength_down", {"code": 26, "e0": False})
            strengthUp   = hotkeys.get("recoil_strength_up",   {"code": 27, "e0": False})
            quitBind     = hotkeys.get("quit",                 {"code": 83, "e0": True})
            rf           = self._fullSettings.get("rapidfire", {})
            rfSlotKeys   = list(rf.get("slot_keys", []))
            weaponSlots  = list(self._settings.get("weapons", []))

        if not isKeyUp:
            if stroke.code == strengthDown["code"] and isE0 == strengthDown["e0"]:
                if "down" not in self._strengthHoldEvents:
                    self._applyStrength(-1)
                    evt = threading.Event()
                    self._strengthHoldEvents["down"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(-1, evt), daemon=True).start()
                return False
            if stroke.code == strengthUp["code"] and isE0 == strengthUp["e0"]:
                if "up" not in self._strengthHoldEvents:
                    self._applyStrength(1)
                    evt = threading.Event()
                    self._strengthHoldEvents["up"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(1, evt), daemon=True).start()
                return False

            # Non-hotkey key-down: try remap
            return self._tryRemap(("key", stroke.code, isE0), False, inter)

        # KEY_UP: cancel hold threads
        if stroke.code == strengthDown["code"] and isE0 == strengthDown["e0"]:
            evt = self._strengthHoldEvents.pop("down", None)
            if evt:
                evt.set()
            return False
        if stroke.code == strengthUp["code"] and isE0 == strengthUp["e0"]:
            evt = self._strengthHoldEvents.pop("up", None)
            if evt:
                evt.set()
            return False

        if stroke.code == overlayBind["code"] and isE0 == overlayBind["e0"]:
            if self._overlayCallback:
                self._overlayCallback()
            return False

        if stroke.code == recoilBind["code"] and isE0 == recoilBind["e0"]:
            with self._lock:
                self._settings["enabled"] = not self._settings["enabled"]
                newState = self._settings["enabled"]
            if self._toggleCallback:
                self._toggleCallback(newState)
            return False

        if stroke.code == quitBind["code"] and isE0 == quitBind["e0"]:
            if self._quitCallback:
                self._quitCallback()
            return False

        # Keyboard slot keys: set armed state based on slot's enabled flag (pass through)
        for sk in rfSlotKeys:
            if sk.get("type") not in ("mouse", "scroll"):
                if sk.get("code") == stroke.code and sk.get("e0", False) == isE0:
                    with self._lock:
                        self._rfArmed = sk.get("enabled", True)
                    return False

        # Recoil weapon slot keys: select active weapon (pass through)
        for i, w in enumerate(weaponSlots):
            if w.get("type") not in ("mouse", "scroll") and w.get("code") is not None:
                if w.get("code") == stroke.code and w.get("e0", False) == isE0:
                    with self._lock:
                        self._activeWeaponIdx = i
                    return False

        # Non-hotkey key-up: try remap
        return self._tryRemap(("key", stroke.code, isE0), True, inter)

    # ------------------------------------------------------------------
    # Remapper
    # ------------------------------------------------------------------

    def _tryRemap(self, sig: tuple, is_up, inter) -> bool:
        """Returns True if the stroke was remapped and should be suppressed."""
        with self._lock:
            remap_cfg     = self._fullSettings.get("remapper", {})
            if not remap_cfg.get("enabled", False):
                return False
            hotkeys       = self._fullSettings.get("hotkeys", {})
            mappings      = list(remap_cfg.get("mappings", []))
            window_filter = self._fullSettings.get("window_filter", "")

            # Protected keys may not be used as FROM
            if sig[0] == "key":
                for hk in ("overlay_toggle", "quit"):
                    bind = hotkeys.get(hk, {})
                    if sig[1] == bind.get("code") and sig[2] == bind.get("e0", False):
                        return False

        if not self.windowMatchesFilter(window_filter):
            return False

        to_input = None
        for mapping in mappings:
            if self._sigMatchesInput(sig, mapping.get("from", {})):
                to_input = mapping.get("to")
                break

        if to_input is None:
            return False

        # Scroll / instantaneous inputs: fire once (no hold state)
        if sig[0] == "scroll" or is_up is None:
            self._sendSynthesized(to_input, False, inter)
            if to_input["type"] == "key":
                self._sendSynthesized(to_input, True, inter)
            return True

        if not is_up:
            self._remapActive[sig] = to_input
            self._sendSynthesized(to_input, False, inter)
        else:
            active_to = self._remapActive.pop(sig, None)
            if active_to is None:
                return False    # key wasn't remapped on down, pass through
            if active_to["type"] != "scroll":
                self._sendSynthesized(active_to, True, inter)

        return True

    def _sigMatchesInput(self, sig: tuple, inp: dict) -> bool:
        t = inp.get("type")
        if t == "key" and sig[0] == "key":
            return sig[1] == inp.get("code") and sig[2] == inp.get("e0", False)
        if t == "mouse" and sig[0] == "mouse":
            return sig[1] == inp.get("button")
        if t == "scroll" and sig[0] == "scroll":
            return sig[1] == inp.get("direction")
        return False

    def windowMatchesFilter(self, filter_name: str) -> bool:
        if not filter_name:
            return True
        if not _WIN32_AVAILABLE:
            return True   # can't check, allow through
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
            return name == filter_name.lower()
        except psutil.NoSuchProcess:
            return False  # process died — don't activate
        except Exception:
            return True   # transient OS error — allow through

    def _sendSynthesized(self, to: dict, is_up: bool, inter=None):
        t = to.get("type")
        try:
            if t == "key":
                flags = 0
                if is_up:
                    flags |= interception.KeyFlag.KEY_UP
                if to.get("e0"):
                    flags |= interception.KeyFlag.KEY_E0
                stroke = interception.KeyStroke(to["code"], flags)
                kb = self._kbDevice or (
                    inter._devices[inter.keyboard]
                    if inter and inter.keyboard < len(inter._devices) else None)
                if kb:
                    kb.send(stroke)

            elif t == "mouse":
                pair = MOUSE_BUTTON_FLAGS.get(to.get("button", ""))
                if pair:
                    flag = pair[1] if is_up else pair[0]
                    stroke = interception.MouseStroke(0, flag, 0, 0, 0)
                    ms = self._msDevice or (
                        inter._devices[inter.mouse]
                        if inter and inter.mouse < len(inter._devices) else None)
                    if ms:
                        ms.send(stroke)

            elif t == "scroll" and not is_up:
                direction = to.get("direction", "up")
                delta = 120 if direction == "up" else -120
                data  = delta if delta > 0 else (delta & 0xFFFF)
                stroke = interception.MouseStroke(0, _SCROLL_WHEEL_FLAG, data, 0, 0)
                ms = self._msDevice or (
                    inter._devices[inter.mouse]
                    if inter and inter.mouse < len(inter._devices) else None)
                if ms:
                    ms.send(stroke)

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Rapid fire loop
    # ------------------------------------------------------------------

    def _rfFireLoop(self):
        while self._running:
            with self._lock:
                should_fire = self._rfFireHeld and self._rfSuppressing
                if should_fire:
                    rf            = self._fullSettings.get("rapidfire", {})
                    interval_ms   = rf.get("interval_ms", 100)
                    humanize      = rf.get("humanize", False)
                    window_filter = self._fullSettings.get("window_filter", "")

            if not should_fire:
                time.sleep(0.01)
                continue

            if self.windowMatchesFilter(window_filter):
                self._sendRFClick()
            if humanize:
                sleep_ms = interval_ms * random.uniform(0.8, 1.2)
            else:
                sleep_ms = interval_ms
            time.sleep(sleep_ms / 1000.0)

    def _sendRFClick(self):
        with self._lock:
            rf           = self._fullSettings.get("rapidfire", {})
            humanize     = rf.get("humanize", False)
            trigger_keys = list(rf.get("trigger_keys", ["mouse_left"]))
        hold_ms = random.randint(30, 60) if humanize else 40

        if not self._msDevice:
            return

        pairs = []
        for key in trigger_keys:
            pair = MOUSE_BUTTON_FLAGS.get(key)
            if pair:
                pairs.append(pair)

        if not pairs:
            return

        try:
            for down_flag, _ in pairs:
                self._msDevice.send(interception.MouseStroke(0, down_flag, 0, 0, 0))
            time.sleep(hold_ms / 1000.0)
            for _, up_flag in pairs:
                self._msDevice.send(interception.MouseStroke(0, up_flag, 0, 0, 0))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Strength helpers
    # ------------------------------------------------------------------

    def _applyStrength(self, direction: int):
        with self._lock:
            weapon = self._activeWeapon()
            weapon["strength_y"] = max(1, min(30, weapon.get("strength_y", 5) + direction))
            newStrength = weapon["strength_y"]
        if self._strengthCallback:
            self._strengthCallback(newStrength)

    def _strengthHoldLoop(self, direction: int, stop_event: threading.Event):
        if stop_event.wait(0.5):
            return
        while not stop_event.wait(0.25):
            self._applyStrength(direction)
