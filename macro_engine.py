"""
MacroEngine — trigger detection, recording, and playback for macros.
handleStroke() is called from RecoilEngine's listen loop on every input event.
"""
import threading
import time
import random

import interception

from recoil import MOUSE_BUTTON_FLAGS, scancodeLabel, windowMatchesFilter

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}


def actionLabel(action: dict) -> tuple:
    """Returns (type_str, value_str) for display in the UI."""
    t = action.get("type", "")
    if t == "delay":
        return "Delay", f"{action.get('ms', 50)} ms"
    if t == "key_tap":
        return "Key Tap",   scancodeLabel(action.get("code", 0), action.get("e0", False))
    if t == "key_down":
        return "Key Down",  scancodeLabel(action.get("code", 0), action.get("e0", False))
    if t == "key_up":
        return "Key Up",    scancodeLabel(action.get("code", 0), action.get("e0", False))
    if t == "mouse_click":
        return "Mouse Click", _MOUSE_DISPLAY.get(action.get("button", ""), action.get("button", ""))
    if t == "mouse_down":
        return "Mouse Down",  _MOUSE_DISPLAY.get(action.get("button", ""), action.get("button", ""))
    if t == "mouse_up":
        return "Mouse Up",    _MOUSE_DISPLAY.get(action.get("button", ""), action.get("button", ""))
    return t, ""


class MacroEngine:

    def __init__(self, settings: dict):
        self._fullSettings  = settings
        self._lock          = threading.Lock()
        self._running       = True

        # Device refs — updated from RecoilEngine listen loop each stroke
        self._kbDevice      = None
        self._msDevice      = None

        # Playback state
        self._playEvent     = threading.Event()
        self._cancelEvent   = threading.Event()
        self._activeMacro   = None
        self._toggleRunning = False
        self._triggerHeld   = False

        # Recording state
        self._recording     = False
        self._recordBuffer  = []   # list of (timestamp_s, action_dict)
        self._recordStart   = 0.0

        self._playThread = threading.Thread(target=self._playbackLoop, daemon=True)
        self._playThread.start()

    # ------------------------------------------------------------------
    # Called from RecoilEngine
    # ------------------------------------------------------------------

    def setDevices(self, kbDevice, msDevice):
        self._kbDevice = kbDevice
        self._msDevice = msDevice

    def updateSettings(self, settings: dict):
        with self._lock:
            self._fullSettings = settings

    @property
    def isRecording(self) -> bool:
        """Used by RecoilEngine's remap-synthesized feedback path to skip
        feeding a synthesized (non-physical) stroke into handleStroke()
        while a macro recording is in progress — recording should only ever
        capture the real physical source stroke (already fed to
        handleStroke() unconditionally from the listen loop), not also the
        remapped-TO stroke synthesized from it, which would otherwise
        double up every recorded remapped key-press into two actions."""
        with self._lock:
            return self._recording

    def handleStroke(self, stroke, is_keyboard: bool, is_e0: bool = False) -> bool:
        """
        Called from interception thread on every stroke.
        Returns False always — macros fire as side effects, never suppress.
        """
        with self._lock:
            recording = self._recording

        if recording:
            self._recordStroke(stroke, is_keyboard, is_e0)
            return False

        # Trigger detection — respects settings["window_filter"] like recoil/remapper
        with self._lock:
            macros = list(self._fullSettings.get("macros", []))
            window_filter = self._fullSettings.get("window_filter", "")

        if not macros:
            return False

        if not windowMatchesFilter(window_filter):
            return False

        for macro in macros:
            if not macro.get("enabled", True):
                continue
            trig      = macro.get("trigger", {})
            trig_type = trig.get("type", "key")

            if is_keyboard and isinstance(stroke, interception.KeyStroke):
                if trig_type != "key":
                    continue
                if trig.get("code") != stroke.code:
                    continue
                if trig.get("e0", False) != is_e0:
                    continue
                is_up = bool(stroke.flags & interception.KeyFlag.KEY_UP)
                self._handleTrigger(is_up, macro)
                break

            elif not is_keyboard and isinstance(stroke, interception.MouseStroke):
                if trig_type != "mouse":
                    continue
                pair = MOUSE_BUTTON_FLAGS.get(trig.get("button", ""))
                if not pair:
                    continue
                is_down = bool(stroke.button_flags & pair[0])
                is_up   = bool(stroke.button_flags & pair[1])
                if not is_down and not is_up:
                    continue
                self._handleTrigger(is_up, macro)
                break

        return False

    def _handleTrigger(self, is_up: bool, macro: dict):
        """Shared hold/toggle/once state machine for both key and mouse triggers."""
        mode = macro.get("mode", "once")
        if mode == "hold":
            if not is_up:
                with self._lock:
                    self._triggerHeld = True
                self._queueMacro(macro)
            else:
                with self._lock:
                    self._triggerHeld = False
                self._cancelEvent.set()
        elif mode == "toggle":
            if not is_up:
                return
            with self._lock:
                running = self._toggleRunning
            if running:
                self._cancelEvent.set()
                with self._lock:
                    self._toggleRunning = False
            else:
                with self._lock:
                    self._toggleRunning = True
                self._queueMacro(macro)
        else:  # once — fire on key-up / button-up
            if is_up:
                self._queueMacro(macro)

    def _queueMacro(self, macro: dict):
        with self._lock:
            self._activeMacro = macro
        self._cancelEvent.clear()
        self._playEvent.set()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def startRecording(self):
        with self._lock:
            self._recording    = True
            self._recordBuffer = []
            self._recordStart  = time.monotonic()

    def stopRecording(self) -> list:
        """Synchronous — safe to call from main thread."""
        with self._lock:
            self._recording = False
            buf = list(self._recordBuffer)
            self._recordBuffer = []
        return self._processRecording(buf)

    def _recordStroke(self, stroke, is_keyboard: bool, is_e0: bool):
        now = time.monotonic()
        with self._lock:
            if not self._recording:
                return
            t = now - self._recordStart
            if is_keyboard and isinstance(stroke, interception.KeyStroke):
                is_up = bool(stroke.flags & interception.KeyFlag.KEY_UP)
                self._recordBuffer.append((t, {
                    "type": "key_up" if is_up else "key_down",
                    "code": stroke.code,
                    "e0":   is_e0,
                }))
            elif not is_keyboard and isinstance(stroke, interception.MouseStroke):
                for name, (dFlag, uFlag) in MOUSE_BUTTON_FLAGS.items():
                    if stroke.button_flags & dFlag:
                        self._recordBuffer.append((t, {"type": "mouse_down", "button": name}))
                    elif stroke.button_flags & uFlag:
                        self._recordBuffer.append((t, {"type": "mouse_up",   "button": name}))

    def _processRecording(self, buf: list) -> list:
        if not buf:
            return []
        actions  = []
        prev_t   = buf[0][0]
        for t, action in buf:
            delay_ms = round((t - prev_t) * 1000)
            if delay_ms > 15:
                actions.append({"type": "delay", "ms": delay_ms})
            actions.append(dict(action))
            prev_t = t
        return actions

    # ------------------------------------------------------------------
    # Test (called from UI thread)
    # ------------------------------------------------------------------

    def testMacro(self, macro: dict):
        self._queueMacro(macro)

    # ------------------------------------------------------------------
    # Playback loop (dedicated thread)
    # ------------------------------------------------------------------

    def _playbackLoop(self):
        while self._running:
            if not self._playEvent.wait(timeout=0.1):
                continue
            self._playEvent.clear()

            with self._lock:
                macro = self._activeMacro
            if macro is None:
                continue

            mode     = macro.get("mode", "once")
            actions  = macro.get("actions", [])
            humanize = macro.get("humanize", False)

            if mode == "hold":
                while True:
                    with self._lock:
                        held = self._triggerHeld
                    if not held or self._cancelEvent.is_set():
                        break
                    self._executeActions(actions, humanize)
                    time.sleep(0.001)  # yield to OS; prevents tight spin if no delays in actions

            elif mode == "toggle":
                while True:
                    with self._lock:
                        running = self._toggleRunning
                    if not running or self._cancelEvent.is_set():
                        break
                    self._executeActions(actions, humanize)
                    time.sleep(0.001)  # yield to OS; prevents tight spin if no delays in actions
                with self._lock:
                    self._toggleRunning = False

            else:  # once
                self._executeActions(actions, humanize)

    def _executeActions(self, actions: list, humanize: bool):
        for action in actions:
            if self._cancelEvent.is_set():
                return
            t = action.get("type", "")
            if t == "delay":
                ms = action.get("ms", 50)
                if humanize:
                    ms = int(ms * random.uniform(0.85, 1.15))
                time.sleep(max(1, ms) / 1000.0)
            elif t in ("key_tap", "key_down", "key_up"):
                self._sendKey(action, t)
            elif t == "mouse_click":
                self._sendMouseClick(action.get("button", "mouse_left"))
            elif t == "mouse_down":
                self._sendMouseButton(action.get("button", "mouse_left"), False)
            elif t == "mouse_up":
                self._sendMouseButton(action.get("button", "mouse_left"), True)

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _sendKey(self, action: dict, action_type: str):
        if not self._kbDevice:
            return
        code = action.get("code", 0)
        if not isinstance(code, int) or not (0 < code <= 255):
            return
        e0      = action.get("e0", False)
        e0_flag = interception.KeyFlag.KEY_E0 if e0 else 0
        try:
            if action_type in ("key_down", "key_tap"):
                self._kbDevice.send(interception.KeyStroke(code, e0_flag))
            if action_type == "key_tap":
                time.sleep(0.03)
            if action_type in ("key_up", "key_tap"):
                self._kbDevice.send(
                    interception.KeyStroke(code, interception.KeyFlag.KEY_UP | e0_flag))
        except Exception:
            pass

    def _sendMouseClick(self, button: str):
        if not self._msDevice:
            return
        pair = MOUSE_BUTTON_FLAGS.get(button)
        if not pair:
            return
        try:
            self._msDevice.send(interception.MouseStroke(0, pair[0], 0, 0, 0))
            time.sleep(0.04)
            self._msDevice.send(interception.MouseStroke(0, pair[1], 0, 0, 0))
        except Exception:
            pass

    def _sendMouseButton(self, button: str, is_up: bool):
        if not self._msDevice:
            return
        pair = MOUSE_BUTTON_FLAGS.get(button)
        if not pair:
            return
        try:
            flag = pair[1] if is_up else pair[0]
            self._msDevice.send(interception.MouseStroke(0, flag, 0, 0, 0))
        except Exception:
            pass

    def stop(self):
        self._running = False
        self._cancelEvent.set()
        self._playEvent.set()
        self._playThread.join(timeout=1.0)
