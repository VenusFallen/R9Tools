import logging
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

# Leading-edge debounce window for RF arm/disarm slot-key toggles — prevents
# a burst of rapid slot-key events (e.g. scrolling through weapons when scroll
# is also bound as an RF slot key) from flickering _rfArmed on/off. The first
# event in a burst toggles immediately; repeats within the window are ignored.
_RF_ARM_DEBOUNCE_SEC = 0.3

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


def windowMatchesFilter(filter_name: str) -> bool:
    """Module-level so non-RecoilEngine consumers (e.g. MacroEngine) can respect
    settings["window_filter"] without needing a RecoilEngine reference."""
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
        self._inputFailedCallback = None
        self.inputEngineFailed    = False  # True once _listenLoop gives up bringing
                                            # up the driver (_bringUpInterception())
                                            # or device I/O fails repeatedly
                                            # mid-session (_onInterceptionIoFailure()).
                                            # UI layer may poll this / hook
                                            # setInputFailedCallback() to surface it.
        self._consecutiveIoFailures = 0    # consecutive failed device I/O calls in
                                            # _listenLoop; reset on any success.
                                            # Deliberately excludes exceptions from
                                            # our own stroke-handling code — see
                                            # _onInterceptionIoFailure().
        self._hotkeysSuspended    = False
        self._strengthHoldEvents: dict = {}   # "down"/"up" → threading.Event
        self._remapActive: dict = {}           # sig tuple → to_input dict
        self._remapPendingRelease: set = set() # sigs whose active remap was force-
                                                # released by updateSettings() while
                                                # still physically held — the next
                                                # matching physical up-event must be
                                                # swallowed rather than passed through
                                                # (see _tryRemap / updateSettings)
        self._toggleActive: dict = {}          # sig tuple -> True while a
                                                # "hold-to-toggle" entry (see
                                                # settings["toggles"]) is logically ON
                                                # for that sig — see
                                                # _lookupToggleTarget/_tryToggleDown
        self._togglePendingRelease: set = set() # sigs whose active toggle was
                                                 # force-released by updateSettings()/
                                                 # stop() while still physically held
                                                 # — mirrors _remapPendingRelease; the
                                                 # next matching physical up-event
                                                 # must be swallowed rather than
                                                 # passed through (see
                                                 # _handleMouseStroke/
                                                 # _handleKeyboardStroke,
                                                 # updateSettings, stop)
        self._kbDevice = None                  # tracked from listen loop for synthesis
        self._msDevice = None                  # tracked from listen loop for synthesis
        self._xDrift   = 0.0                   # Brownian X drift accumulator

        self._interception     = None          # live Interception context, set by
                                                 # _listenLoop as soon as bring-up
                                                 # succeeds; destroyed exactly once by
                                                 # whichever of stop()/_listenLoop's own
                                                 # cleanup gets to it first — see
                                                 # _destroyInterception().
        self._interceptionLock = threading.Lock()  # guards _interception specifically;
                                                     # separate from self._lock (which
                                                     # guards settings/state) so stop()
                                                     # can destroy the context immediately
                                                     # without contending with whatever
                                                     # the listen thread is doing under
                                                     # self._lock at that instant.

        self._rfArmed        = False   # True when weapon slot key has been toggled on
        self._lastRfArmToggleTime = None  # time.monotonic() of last accepted slot-key
                                           # toggle, for the debounce below; None means
                                           # "no toggle yet" so the first one always fires
        self._rfFireHeld     = False   # True while all RF trigger keys are physically held
        self._rfSuppressing  = False   # True when we have suppressed the fire trigger
        self._activeWeaponIdx = 0      # index into recoil.weapons list
        self._macroEngine    = None    # set via setMacroEngine() after construction
        self._lastKbDevice   = None    # last device refs sent to macro engine
        self._lastMsDevice   = None

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

    def setInputFailedCallback(self, cb):
        """cb() is called (from the listen thread) if the Interception driver
        context can't be brought up after exhausting retries — see
        _bringUpInterception(). No hotkeys/remaps/macros/mouse-forwarding
        work at all when this happens, so this is a hard, user-visible-worthy
        failure, not a transient hiccup."""
        self._inputFailedCallback = cb

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
        # Force-release any active toggle or remap so it doesn't get stuck
        # held down in whatever app/game has focus after we stop listening.
        # Must run before _destroyInterception(): the synthesized release
        # needs a still-live device handle to send through.
        with self._lock:
            stuck_toggles = self._forceReleaseActiveToggles_locked()
            stuck_remaps  = self._forceReleaseActiveRemaps_locked()
        for sig in stuck_toggles:
            self._sendSynthesized(self._toggleOwnInput(sig), True, None)
        for to_input in stuck_remaps.values():
            if to_input.get("type") != "scroll":
                self._sendSynthesized(to_input, True, None)

        # Destroy the Interception context FIRST, before any thread joins.
        # The driver service stays running permanently (see
        # _interception_driver in main.py); what actually releases
        # system-wide input is closing this process's own filtered device
        # handles, which is fast — do it immediately rather than gating it
        # behind thread-join timeouts.
        self._destroyInterception()
        self._applyThread.join(timeout=1.0)
        self._listenThread.join(timeout=1.0)
        self._rfThread.join(timeout=1.0)

    def _destroyInterception(self) -> None:
        """Destroy self._interception exactly once, whether reached from
        stop() or _listenLoop's own cleanup racing against it — both paths
        funnel through here. `_interceptionLock` is separate from
        self._lock so stop() can run this without contending with whatever
        the listen thread is doing at that instant."""
        with self._interceptionLock:
            inter = self._interception
            self._interception = None
        if inter is None:
            return
        try:
            inter.destroy()
        except Exception:
            # Best-effort cleanup — must never raise out of stop() or the
            # listen loop's exit path.
            logging.getLogger("r9tools.recoil").exception(
                "Interception.destroy() raised during cleanup"
            )

    def updateSettings(self, settings: dict, full_reset: bool = False):
        """Swap in new settings. `full_reset=True` (profile load/switch)
        wipes all armed/held/suppressing state to a clean slate.
        `full_reset=False` (default, used by every UI panel's routine
        settings-changed callback) instead diffs against the old settings
        and only resets state actually invalidated by what changed, so an
        unrelated tweak mid-match doesn't silently disarm Rapid Fire."""
        with self._lock:
            active_remaps = dict(self._remapActive)
            # Stop any running strength hold threads before swapping settings
            for evt in self._strengthHoldEvents.values():
                evt.set()
            self._strengthHoldEvents.clear()

            old_rf     = self._fullSettings.get("rapidfire", {})
            new_rf     = settings.get("rapidfire", {})
            new_recoil = settings.get("recoil", {})

            self._fullSettings   = settings
            self._settings       = settings["recoil"]
            # Any sig still in active_remaps was physically held when settings
            # changed; its synthesized target release happens below, but the
            # ORIGINAL physical key is still held and its eventual real
            # up-event needs to be swallowed rather than passed through raw —
            # track it here so _tryRemap can do that.
            self._remapPendingRelease.update(self._remapActive.keys())
            self._remapActive.clear()

            # Unlike remap-active (cleared unconditionally, since it only
            # ever spans the instant a key is held), a toggle's ON state is
            # meant to persist across unrelated settings changes — only
            # force-release it on full_reset, or if its binding was
            # disabled/removed in the incoming settings.
            new_toggles_cfg = settings.get("toggles", [])
            if full_reset:
                stuck_toggles = self._forceReleaseActiveToggles_locked()
            else:
                invalidated = [
                    sig for sig in self._toggleActive
                    if not self._toggleStillEnabled_locked(sig, new_toggles_cfg)
                ]
                stuck_toggles = {sig: True for sig in invalidated}
                for sig in invalidated:
                    del self._toggleActive[sig]
                # Same "swallow the next stray physical up" rationale as
                # _remapPendingRelease above — see its comment.
                self._togglePendingRelease.update(stuck_toggles.keys())

            if full_reset:
                self._held.clear()
                self._rfArmed             = False
                self._lastRfArmToggleTime = None
                self._rfFireHeld          = False
                self._rfSuppressing       = False
                self._activeWeaponIdx     = 0
            else:
                # Slot-key bindings changed structurally — the old armed
                # state was earned via bindings that may no longer exist.
                if new_rf.get("slot_keys") != old_rf.get("slot_keys"):
                    self._rfArmed             = False
                    self._lastRfArmToggleTime = None

                # Trigger-key bindings changed structurally — the key-up
                # handler checks membership against the *new* trigger_keys,
                # so a held/suppressing state tied to the old keys would
                # otherwise get stuck since their release would never clear it.
                if new_rf.get("trigger_keys") != old_rf.get("trigger_keys"):
                    self._rfFireHeld    = False
                    self._rfSuppressing = False

                # Weapon list shrank such that the active index no longer
                # points at a real entry — clamp it back instead of leaving
                # it dangling.
                new_weapons = new_recoil.get("weapons", [])
                if not new_weapons or self._activeWeaponIdx >= len(new_weapons):
                    self._activeWeaponIdx = 0

                # _held deliberately isn't cleared here — it mirrors
                # physically-held keys tracked live by the listen loop, so
                # clearing it would falsely drop an in-progress trigger hold.

        # Release any remapped outputs that were held at settings-change time
        for to_input in active_remaps.values():
            if to_input.get("type") in ("key", "mouse"):
                self._sendSynthesized(to_input, True, None)

        # Release any toggles force-cleared above (see stuck_toggles comment)
        for sig in stuck_toggles:
            self._sendSynthesized(self._toggleOwnInput(sig), True, None)

    @property
    def isActive(self) -> bool:
        with self._lock:
            combo = set(self._settings["trigger_keys"])
            return combo.issubset(self._held)

    @property
    def rfArmed(self) -> bool:
        with self._lock:
            if not self._fullSettings.get("rapidfire", {}).get("slot_keys"):
                # No weapon slots configured — RF has nothing to arm against,
                # so it's implicitly armed whenever it's enabled (see
                # _rfShouldActivate_locked for the matching activation rule).
                return True
            return self._rfArmed

    @property
    def rfFiring(self) -> bool:
        with self._lock:
            return self._rfFireHeld and self._rfSuppressing

    def _tryToggleRfArmed_locked(self) -> None:
        """Toggle self._rfArmed, gated by _RF_ARM_DEBOUNCE_SEC. Must be
        called with self._lock held. Leading-edge debounce: the first
        slot-key toggle in a burst is applied immediately; any further
        slot-key events (mouse, scroll, or keyboard — shared timestamp)
        within the debounce window are ignored outright, so a rapid burst
        (e.g. scrolling through several weapons) doesn't flicker the armed
        state. The clock isn't restarted by ignored events, so the next
        accepted toggle is still measured from the last real one."""
        now = time.monotonic()
        if (self._lastRfArmToggleTime is not None and
                now - self._lastRfArmToggleTime < _RF_ARM_DEBOUNCE_SEC):
            return
        self._rfArmed = not self._rfArmed
        self._lastRfArmToggleTime = now

    def _rfShouldActivate_locked(self) -> bool:
        """Check RF conditions. Must be called with self._lock held."""
        rf = self._fullSettings.get("rapidfire", {})
        if not rf.get("enabled", False):
            return False
        if not rf.get("slot_keys"):
            # No weapon slots configured — don't gate on the arm state, since
            # nothing can ever set self._rfArmed True in that case and RF
            # would otherwise be permanently inert despite being "enabled".
            return True
        return self._rfArmed

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

    # Bounded retry budget for bringing up the Interception driver context at
    # startup — see _bringUpInterception() for why this is needed. ~20
    # attempts x up to 250ms backoff caps out around 10s worst case, enough
    # to cover driver-service-start settling without hanging indefinitely.
    _INTERCEPTION_BRINGUP_ATTEMPTS = 20
    _INTERCEPTION_BRINGUP_BASE_DELAY = 0.05  # seconds, doubles each retry up to a cap
    _INTERCEPTION_BRINGUP_MAX_DELAY = 0.5

    # Consecutive-failure budget for the driver's device I/O calls once the
    # listen loop is already running — the mid-session counterpart to
    # _INTERCEPTION_BRINGUP_ATTEMPTS, detecting the driver dying after a
    # successful startup. At the ~100ms poll cadence, 50 failures is ~5s of
    # sustained failure — long enough to rule out a single transient hiccup.
    _INTERCEPTION_IO_FAILURE_THRESHOLD = 50

    def _bringUpInterception(self):
        """Construct an interception.Interception() context and apply the
        keyboard/mouse filters, retrying with backoff on failure.

        interception-python's Interception.__init__() silently swallows
        exceptions while opening its 20 device handles, and set_filter()
        then indexes them with zero bounds checking — a partial open turns
        into an unhandled IndexError. This has been observed as a
        fresh-install-time crash: `sc start` (see _interception_driver in
        main.py) only guarantees the driver's service reached
        SERVICE_RUNNING, not that it has finished attaching to the
        keyboard/mouse device stacks, so retrying with backoff gives that
        settling window a chance to close.

        Returns the ready Interception instance, or None if every retry was
        exhausted.
        """
        delay = self._INTERCEPTION_BRINGUP_BASE_DELAY
        lastErr = None
        for attempt in range(1, self._INTERCEPTION_BRINGUP_ATTEMPTS + 1):
            inter = None
            try:
                inter = interception.Interception()
                # A fully-settled driver context always has all 20 device
                # slots open — a short list means construction "succeeded"
                # but the context is actually unusable.
                if len(inter._devices) < 20:
                    raise RuntimeError(
                        f"Interception context incomplete: "
                        f"{len(inter._devices)}/20 device handles opened"
                    )
                inter.set_filter(
                    inter.is_mouse,
                    interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL
                )
                inter.set_filter(
                    inter.is_keyboard,
                    interception.FilterKeyFlag.FILTER_KEY_ALL
                )
                if attempt > 1:
                    logging.getLogger("r9tools.recoil").warning(
                        "Interception driver context came up on attempt %d/%d",
                        attempt, self._INTERCEPTION_BRINGUP_ATTEMPTS,
                    )
                return inter
            except Exception as exc:
                lastErr = exc
                if inter is not None:
                    try:
                        inter.destroy()
                    except Exception:
                        pass
                if not self._running:
                    return None  # stop() was called while we were retrying
                time.sleep(delay)
                delay = min(delay * 2, self._INTERCEPTION_BRINGUP_MAX_DELAY)

        logging.getLogger("r9tools.recoil").critical(
            "Interception driver context failed to come up after %d attempts "
            "— input engine cannot start (no hotkeys, remaps, macros, or "
            "recoil will work this session). Last error: %r",
            self._INTERCEPTION_BRINGUP_ATTEMPTS, lastErr,
        )
        return None

    def _onInterceptionIoFailure(self) -> None:
        """Record one failed device I/O call (await_input/receive/send)
        and, once _INTERCEPTION_IO_FAILURE_THRESHOLD consecutive failures
        are seen, mark the input engine failed and fire
        setInputFailedCallback(). Deliberately narrow: exceptions from our
        own stroke-handling logic are NOT counted here — those are software
        bugs, not evidence the driver died, and must not be misreported as
        "restart your PC". Fires the callback exactly once per transition."""
        self._consecutiveIoFailures += 1
        if (self._consecutiveIoFailures < self._INTERCEPTION_IO_FAILURE_THRESHOLD
                or self.inputEngineFailed):
            return

        self.inputEngineFailed = True
        logging.getLogger("r9tools.recoil").critical(
            "Interception device I/O failed %d consecutive times — the "
            "driver appears to have died mid-session; input engine marked "
            "failed.",
            self._consecutiveIoFailures,
        )
        if self._inputFailedCallback:
            try:
                self._inputFailedCallback()
            except Exception:
                logging.getLogger("r9tools.recoil").exception(
                    "inputFailedCallback raised"
                )

    def _listenLoop(self):
        inter = self._bringUpInterception()
        if inter is None:
            self.inputEngineFailed = True
            if self._inputFailedCallback:
                try:
                    self._inputFailedCallback()
                except Exception:
                    logging.getLogger("r9tools.recoil").exception(
                        "inputFailedCallback raised"
                    )
            return

        with self._interceptionLock:
            self._interception = inter

        try:
            while self._running:
                try:
                    # Device I/O: the only calls that count toward
                    # _onInterceptionIoFailure()'s failure tracking, so each
                    # is wrapped individually rather than relying on the
                    # outer per-iteration except below.
                    try:
                        deviceIdx = inter.await_input(100)
                    except Exception:
                        self._onInterceptionIoFailure()
                        raise
                    if deviceIdx is None:
                        # Clean, expected idle timeout — normal behavior,
                        # not a failure. Reset the streak.
                        self._consecutiveIoFailures = 0
                        continue

                    if deviceIdx >= len(inter._devices):
                        continue
                    device = inter._devices[deviceIdx]
                    try:
                        stroke = device.receive()
                    except Exception:
                        self._onInterceptionIoFailure()
                        raise
                    # A successful receive() (even one returning None —
                    # nothing to process this tick) proves the I/O layer is
                    # alive — reset the streak.
                    self._consecutiveIoFailures = 0
                    if stroke is None:
                        continue

                    # Track devices for use in synthesis
                    is_kb = inter.is_keyboard(deviceIdx)
                    if is_kb:
                        self._kbDevice = device
                    elif inter.is_mouse(deviceIdx):
                        self._msDevice = device

                    # Keep macro engine device refs current (only when they actually change)
                    if self._macroEngine:
                        if (self._kbDevice is not self._lastKbDevice
                                or self._msDevice is not self._lastMsDevice):
                            self._lastKbDevice = self._kbDevice
                            self._lastMsDevice = self._msDevice
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
                        try:
                            device.send(stroke)
                        except Exception:
                            self._onInterceptionIoFailure()
                            raise
                except Exception:
                    # Never let a single bad stroke/handler bug silently kill
                    # the input thread — log and keep listening. This also
                    # makes it safe for stop() (another thread) to destroy
                    # `inter` out from under a blocked await_input/receive/
                    # send call: the exception lands here, gets logged, and
                    # the loop exits cleanly via `self._running` on the next
                    # iteration. I/O-call exceptions are already counted in
                    # _onInterceptionIoFailure() before landing here; our own
                    # stroke-handling exceptions are not (see its docstring).
                    logging.getLogger("r9tools.recoil").exception(
                        "Unhandled error in _listenLoop iteration — continuing"
                    )
        finally:
            # Reached on any exit — normal stop(), or an error not caught by
            # the per-iteration try/except above. No-op if stop() already
            # won the race and cleared self._interception.
            self._destroyInterception()

    def _handleMouseStroke(self, stroke, inter) -> bool:
        # A signature that resolves to an active remap must NOT also
        # register under its own physical identity (_held, RF keys) — only
        # the remapped-TO identity should, via
        # _registerSynthesizedMouseFeedback/_registerSynthesizedScrollFeedback.
        for key, (downFlag, upFlag) in MOUSE_BUTTON_FLAGS.items():
            sig = ("mouse", key)
            if stroke.button_flags & downFlag:
                if self._lookupRemapTarget(sig) is not None:
                    return self._tryRemap(sig, False, inter)
                # Toggle: checked after remap (first-branch-wins precedent
                # — a key configured as both a remap-FROM and a toggle is
                # not a supported combination), before today's normal
                # _coreMouseButtonDown handling. See _tryToggleDown.
                if self._lookupToggleTarget(sig) is not None:
                    return self._tryToggleDown(sig, inter)
                if self._coreMouseButtonDown(key):
                    return True
                return self._tryRemap(sig, False, inter)

            elif stroke.button_flags & upFlag:
                with self._lock:
                    remap_relevant = (sig in self._remapActive
                                       or sig in self._remapPendingRelease)
                if remap_relevant:
                    return self._tryRemap(sig, True, inter)
                with self._lock:
                    toggle_pending = sig in self._togglePendingRelease
                    if toggle_pending:
                        self._togglePendingRelease.discard(sig)
                if toggle_pending:
                    return True
                if self._lookupToggleTarget(sig) is not None:
                    # A toggle-configured identity: the only up-events the
                    # game should ever see are the synthesized ones from
                    # _tryToggleDown's ON->OFF branch, so a raw physical up
                    # is always suppressed here regardless of ON/OFF state.
                    return True
                if self._coreMouseButtonUp(key):
                    return True
                return self._tryRemap(sig, True, inter)

        # Scroll wheel
        if stroke.button_flags & _SCROLL_WHEEL_FLAG:
            delta = stroke.button_data
            if delta > 32767:   # interpret uint16 as signed
                delta -= 65536
            direction = "up" if delta > 0 else "down"
            if self._lookupRemapTarget(("scroll", direction)) is None:
                self._coreScroll(direction)
            return self._tryRemap(("scroll", direction), None, inter)

        return False

    def _coreMouseButtonDown(self, key: str) -> bool:
        """Update _held + RF trigger_keys state for a mouse button identity
        going down. Shared by the physical (non-remapped) path above and
        _registerSynthesizedMouseFeedback below. Returns True if RF has
        taken over firing (the caller should treat the event as suppressed);
        for the feedback path the return value is unused, since the actual
        OS-level send already happened in _sendSynthesized."""
        is_rf_trig      = False
        should_activate = False
        wf              = ""
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
        return False

    def _coreMouseButtonUp(self, key: str) -> bool:
        """Update _held + RF trigger_keys/slot_keys/weapon-select state for a
        mouse button identity going up. Shared by the physical (non-
        remapped) path above and _registerSynthesizedMouseFeedback below."""
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
            # Mouse slot keys: toggle RF armed state on release. Each
            # configured slot key independently toggles the single shared
            # _rfArmed flag (no per-key arm state).
            for sk in slot_keys:
                if sk.get("type") == "mouse" and sk.get("button") == key:
                    if sk.get("enabled", True):
                        self._tryToggleRfArmed_locked()
                    break
            # Mouse weapon slots: select active weapon
            for i, w in enumerate(weaponSlots):
                if w.get("type") == "mouse" and w.get("button") == key:
                    self._activeWeaponIdx = i
                    break
        return suppress_rf

    def _coreScroll(self, direction: str) -> None:
        """RF slot_keys arm/disarm toggle + weapon-select slot matching for a
        scroll-wheel identity. Scroll has no physical "held" state, so
        unlike the mouse-button/keyboard cores above this is a momentary
        check only — nothing is added to/removed from _held. Shared by the
        physical (non-remapped) path above and
        _registerSynthesizedScrollFeedback below."""
        with self._lock:
            rf          = self._fullSettings.get("rapidfire", {})
            slot_keys   = list(rf.get("slot_keys", []))
            weaponSlots = list(self._settings.get("weapons", []))
            # Scroll slot keys: toggle RF armed state (see mouse slot-key
            # handler above for the toggle-vs-force rationale).
            for sk in slot_keys:
                if sk.get("type") == "scroll" and sk.get("direction") == direction:
                    if sk.get("enabled", True):
                        self._tryToggleRfArmed_locked()
                    break
            # Scroll weapon slots: select active weapon
            for i, w in enumerate(weaponSlots):
                if w.get("type") == "scroll" and w.get("direction") == direction:
                    self._activeWeaponIdx = i
                    break

    def _handleKeyboardStroke(self, stroke, inter) -> bool:
        isKeyUp = bool(stroke.flags & interception.KeyFlag.KEY_UP)
        isE0    = bool(stroke.flags & interception.KeyFlag.KEY_E0)
        label   = scancodeLabel(stroke.code, isE0)
        sig     = ("key", stroke.code, isE0)

        with self._lock:
            hotkeysSuspended = self._hotkeysSuspended

        if hotkeysSuspended:
            # Remapping can never apply while hotkeys are suspended (this
            # function always returns before reaching remap-lookup below in
            # that state), so held-tracking always mirrors the raw physical
            # key here — unchanged from the pre-existing behavior.
            self._updateHeldKeyboard(label, isKeyUp)
            return False

        with self._lock:
            hotkeys      = self._fullSettings.get("hotkeys", {})
            overlayBind  = hotkeys.get("overlay_toggle",       {"code": 82, "e0": True})
            recoilBind   = hotkeys.get("recoil_toggle",        {"code": 68, "e0": False})
            strengthDown = hotkeys.get("recoil_strength_down", {"code": 26, "e0": False})
            strengthUp   = hotkeys.get("recoil_strength_up",   {"code": 27, "e0": False})
            quitBind     = hotkeys.get("quit",                 {"code": 83, "e0": True})

        if not isKeyUp:
            if stroke.code == strengthDown["code"] and isE0 == strengthDown["e0"]:
                self._updateHeldKeyboard(label, False)
                if "down" not in self._strengthHoldEvents:
                    self._applyStrength(-1)
                    evt = threading.Event()
                    self._strengthHoldEvents["down"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(-1, evt), daemon=True).start()
                return False
            if stroke.code == strengthUp["code"] and isE0 == strengthUp["e0"]:
                self._updateHeldKeyboard(label, False)
                if "up" not in self._strengthHoldEvents:
                    self._applyStrength(1)
                    evt = threading.Event()
                    self._strengthHoldEvents["up"] = evt
                    threading.Thread(target=self._strengthHoldLoop,
                                     args=(1, evt), daemon=True).start()
                return False

            # Non-hotkey key-down: registers in _held/trigger-detection under
            # its OWN signature only if it will NOT be remapped — otherwise
            # the remapped-TO identity registers instead, via
            # _registerSynthesizedKeyFeedback (see _sendSynthesized).
            if self._lookupRemapTarget(sig) is not None:
                return self._tryRemap(sig, False, inter)

            # Toggle: checked after remap (first-branch-wins precedent — a
            # key configured as both a remap-FROM and a toggle is not a
            # supported combination), before today's normal
            # _updateHeldKeyboard handling. See _tryToggleDown.
            if self._lookupToggleTarget(sig) is not None:
                return self._tryToggleDown(sig, inter)

            self._updateHeldKeyboard(label, False)
            return self._tryRemap(sig, False, inter)

        # KEY_UP
        with self._lock:
            remap_relevant = sig in self._remapActive or sig in self._remapPendingRelease

        if remap_relevant:
            return self._tryRemap(sig, True, inter)

        with self._lock:
            toggle_pending = sig in self._togglePendingRelease
            if toggle_pending:
                self._togglePendingRelease.discard(sig)
        if toggle_pending:
            return True
        if self._lookupToggleTarget(sig) is not None:
            # See the matching comment in _handleMouseStroke's up-branch —
            # a raw physical up for a toggle-configured identity is always
            # suppressed, regardless of current ON/OFF state.
            return True

        self._updateHeldKeyboard(label, True)

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

        # Keyboard RF slot keys / weapon-select slot keys: toggle armed state
        # or select active weapon on release (pass through).
        if self._applyKeyboardSlotAndWeaponSelect(stroke.code, isE0):
            return False

        # Non-hotkey key-up: try remap
        return self._tryRemap(sig, True, inter)

    def _updateHeldKeyboard(self, label: str, is_up: bool) -> None:
        """Update _held for a keyboard key identity. Shared by the physical
        (non-remapped/non-suspended) path above and
        _registerSynthesizedKeyFeedback below."""
        with self._lock:
            if is_up:
                self._held.discard(label)
            else:
                self._held.add(label)

    def _applyKeyboardSlotAndWeaponSelect(self, code: int, e0: bool) -> bool:
        """RF slot_keys arm/disarm toggle + weapon-select slot matching for a
        keyboard key identity (release-triggered). Shared by the physical
        key-up handler above and _registerSynthesizedKeyFeedback below.
        Returns True if a slot-key or weapon-select binding matched (mirrors
        the physical handler's "already handled, don't also try remap"
        outcome)."""
        with self._lock:
            rf          = self._fullSettings.get("rapidfire", {})
            rfSlotKeys  = list(rf.get("slot_keys", []))
            weaponSlots = list(self._settings.get("weapons", []))

            # Keyboard slot keys: toggle RF armed state on release, gated by
            # the slot's enabled flag. See mouse slot-key handler above for
            # the toggle-vs-force rationale.
            for sk in rfSlotKeys:
                if sk.get("type") not in ("mouse", "scroll"):
                    if sk.get("code") == code and sk.get("e0", False) == e0:
                        if sk.get("enabled", True):
                            self._tryToggleRfArmed_locked()
                        return True

            # Recoil weapon slot keys: select active weapon
            for i, w in enumerate(weaponSlots):
                if w.get("type") not in ("mouse", "scroll") and w.get("code") is not None:
                    if w.get("code") == code and w.get("e0", False) == e0:
                        self._activeWeaponIdx = i
                        return True

        return False

    # ------------------------------------------------------------------
    # Remapper
    # ------------------------------------------------------------------

    def _tryRemap(self, sig: tuple, is_up, inter) -> bool:
        """Returns True if the stroke was remapped and should be suppressed."""
        if is_up:
            with self._lock:
                # This sig's active remap was force-released by a concurrent
                # updateSettings() call while still physically held (see
                # _remapPendingRelease). The game already got a synthesized
                # up-event for the remap target, so this stray physical up
                # must be swallowed rather than forwarded raw.
                if sig in self._remapPendingRelease:
                    self._remapPendingRelease.discard(sig)
                    return True
                active_to = self._remapActive.pop(sig, None)
            if active_to is None:
                return False    # key wasn't remapped on down, pass through
            if active_to["type"] != "scroll":
                self._sendSynthesized(active_to, True, inter)
            return True

        # is_up is False (down) or None (scroll / instantaneous input)
        to_input = self._lookupRemapTarget(sig)
        if to_input is None:
            return False

        # Scroll / instantaneous inputs have no physical "held" state — fire
        # a full press+release for the target instead so nothing is left
        # stuck down (either at the OS level or in the feedback-driven
        # _held/trigger state below). The release call is a safe no-op for
        # scroll-type targets, which only ever synthesize anything on the
        # "down" call — see _sendSynthesized.
        if sig[0] == "scroll" or is_up is None:
            self._sendSynthesized(to_input, False, inter)
            self._sendSynthesized(to_input, True, inter)
            return True

        with self._lock:
            self._remapActive[sig] = to_input
        self._sendSynthesized(to_input, False, inter)
        return True

    def _forceReleaseActiveRemaps_locked(self) -> dict:
        """Pop and clear every currently-active remap. Must be called with
        self._lock already held. Returns the popped {sig: to_input}
        mapping; the caller must call _sendSynthesized(to_input, True, ...)
        for each non-"scroll" value AFTER releasing the lock.

        This is a stop()-only cleanup — updateSettings() already
        force-releases every active remap unconditionally on every call
        (a remap's active state only ever spans the instant a key is held,
        unlike a toggle's persistent ON state), so only quitting outright
        (which never runs updateSettings()) needs this. self.
        _remapPendingRelease is deliberately left untouched: there is no
        future physical up-event to swallow once the input thread and
        driver context are being torn down as part of this same stop()."""
        stuck = dict(self._remapActive)
        self._remapActive.clear()
        return stuck

    def _lookupRemapTarget(self, sig: tuple):
        """Read-only lookup: does `sig` currently resolve to a configured,
        active remap mapping (remapper enabled, not a protected hotkey,
        foreground window matches the filter)? Returns the "to" dict, or
        None. No state mutation or sending — safe to call purely to decide
        which path a stroke handler should take."""
        with self._lock:
            remap_cfg = self._fullSettings.get("remapper", {})
            if not remap_cfg.get("enabled", False):
                return None
            hotkeys       = self._fullSettings.get("hotkeys", {})
            mappings      = list(remap_cfg.get("mappings", []))
            window_filter = self._fullSettings.get("window_filter", "")

            # Protected keys may not be used as FROM
            if sig[0] == "key":
                for hk in ("overlay_toggle", "quit"):
                    bind = hotkeys.get(hk, {})
                    if sig[1] == bind.get("code") and sig[2] == bind.get("e0", False):
                        return None

        if not self.windowMatchesFilter(window_filter):
            return None

        for mapping in mappings:
            if self._sigMatchesInput(sig, mapping.get("from", {})):
                return mapping.get("to")
        return None

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
        return windowMatchesFilter(filter_name)

    # ------------------------------------------------------------------
    # Hold-to-toggle (settings["toggles"]): converts a key/button's native
    # hold behavior into press-once-to-hold-logically-until-pressed-again.
    # Checked after remap, before the normal core handlers (same
    # first-branch-wins precedent remap already establishes). Down while
    # OFF passes through unmodified and marks ON; down while ON suppresses
    # the physical down and synthesizes a release for the same identity,
    # marking OFF. Physical up is ALWAYS suppressed — the only up-events
    # the game sees are the synthesized ones from the down-handler, which
    # is what makes "on" persist after the finger lifts. A key configured
    # as both remap-FROM and toggle is unsupported; remap wins silently.
    # ------------------------------------------------------------------

    def _lookupToggleTarget(self, sig: tuple):
        """Read-only lookup: does `sig` currently resolve to a configured,
        enabled toggle entry (settings["toggles"]), respecting
        window_filter and the same Menu-Toggle/Quit protection remap's
        _lookupRemapTarget enforces? Returns the matching toggle dict, or
        None. No state mutation — safe to call purely to decide which
        branch a stroke handler should take."""
        if sig[0] not in ("key", "mouse"):
            # Scroll is inert for toggles — an instantaneous tick has no
            # meaningful "held" state to toggle. Defense in depth; malformed
            # scroll-type toggle entries should already be filtered out by
            # _sanitize_profile.
            return None

        with self._lock:
            toggles       = list(self._fullSettings.get("toggles", []))
            hotkeys       = self._fullSettings.get("hotkeys", {})
            window_filter = self._fullSettings.get("window_filter", "")

            # Protected keys may not be used as a toggle binding — mirrors
            # _lookupRemapTarget's identical block for remap-FROM above.
            if sig[0] == "key":
                for hk in ("overlay_toggle", "quit"):
                    bind = hotkeys.get(hk, {})
                    if sig[1] == bind.get("code") and sig[2] == bind.get("e0", False):
                        return None

        if not self.windowMatchesFilter(window_filter):
            return None

        for t in toggles:
            if not t.get("enabled", True):
                continue
            if t.get("type") not in ("key", "mouse"):
                continue  # "scroll" (or malformed) entries are inert
            if self._sigMatchesInput(sig, t):
                return t
        return None

    def _toggleStillEnabled_locked(self, sig: tuple, toggles: list) -> bool:
        """Structural-only check (no window_filter/protected-hotkey gating,
        unlike _lookupToggleTarget) — does `sig` still match an enabled
        entry in the given toggles list? Used by updateSettings() to decide
        whether an active toggle's binding was invalidated by incoming
        settings. Must be called with self._lock held."""
        for t in toggles:
            if not t.get("enabled", True):
                continue
            if t.get("type") not in ("key", "mouse"):
                continue
            if self._sigMatchesInput(sig, t):
                return True
        return False

    def _forceReleaseActiveToggles_locked(self) -> dict:
        """Pop and clear every currently-active toggle, marking each sig for
        stray-up swallowing via self._togglePendingRelease. Must be called
        with self._lock already held. Returns the popped {sig: True}
        mapping; the caller must call
        _sendSynthesized(self._toggleOwnInput(sig), True, ...) for each
        returned sig AFTER releasing the lock. Shared by stop() and
        updateSettings()'s full_reset path — the two wipe-everything
        scenarios; a single disabled/removed toggle on a routine settings
        change is handled inline in updateSettings() instead."""
        stuck = dict(self._toggleActive)
        self._toggleActive.clear()
        self._togglePendingRelease.update(stuck.keys())
        return stuck

    def _toggleOwnInput(self, sig: tuple) -> dict:
        """Build a _sendSynthesized()-shaped 'to' dict representing the
        toggle's own physical identity — used to synthesize the release
        when a toggle turns OFF, or is force-released (stuck-key cleanup
        in updateSettings()/stop()). Unlike remap, a toggle's "to" target
        IS its own "from" binding, so this is built directly from the sig
        tuple rather than looked up from a separate mapping dict."""
        if sig[0] == "key":
            return {"type": "key", "code": sig[1], "e0": sig[2]}
        return {"type": "mouse", "button": sig[1]}

    def _tryToggleDown(self, sig: tuple, inter) -> bool:
        """Down-edge state machine for a sig that resolves to an enabled
        toggle (see _lookupToggleTarget) — see the class-level comment
        block above for the full state machine. Returns True if the
        physical down should be suppressed."""
        with self._lock:
            is_on = sig in self._toggleActive
            if not is_on:
                self._toggleActive[sig] = True
            else:
                del self._toggleActive[sig]

        if not is_on:
            # OFF -> ON: let the real physical down through unmodified and
            # register this identity as held for RF/recoil trigger
            # detection. Calls the *core* handler directly (not
            # _registerSynthesizedKeyFeedback/Mouse) since the macro engine
            # already saw this physical stroke via _listenLoop's raw feed —
            # re-feeding it would double-count it. A toggle key that's also
            # an RF trigger key is unsupported; the toggle state machine
            # always wins over RF's own suppress decision on the DOWN edge.
            if sig[0] == "mouse":
                self._coreMouseButtonDown(sig[1])
            else:
                self._updateHeldKeyboard(scancodeLabel(sig[1], sig[2]), False)
            return False

        # ON -> OFF: suppress the physical down, synthesize a release for
        # the SAME identity — _sendSynthesized's feedback plumbing removes
        # it from _held and feeds the macro engine with the synthesized
        # up-stroke.
        self._sendSynthesized(self._toggleOwnInput(sig), True, inter)
        return True

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
                    self._registerSynthesizedKeyFeedback(
                        to["code"], bool(to.get("e0")), is_up, stroke)

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
                        self._registerSynthesizedMouseFeedback(
                            to.get("button", ""), is_up, stroke)

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
                    self._registerSynthesizedScrollFeedback(direction, stroke)

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Remap-synthesized feedback — makes a remapped-TO input's synthesized
    # down/up register as real input for recoil/RF/macro trigger detection,
    # exactly as a physical stroke carrying that same identity would. Never
    # re-enters _tryRemap (so a remap target chained to another remap's
    # FROM can't double-translate) and never sends — the OS-level send
    # already happened in _sendSynthesized just before these are called.
    # ------------------------------------------------------------------

    def _registerSynthesizedKeyFeedback(self, code: int, e0: bool, is_up: bool, stroke) -> None:
        label = scancodeLabel(code, e0)
        try:
            if not is_up:
                self._updateHeldKeyboard(label, False)
            else:
                self._updateHeldKeyboard(label, True)
                self._applyKeyboardSlotAndWeaponSelect(code, e0)
        except Exception:
            return
        self._feedMacroEngine(stroke, True, e0)

    def _registerSynthesizedMouseFeedback(self, button: str, is_up: bool, stroke) -> None:
        try:
            if not is_up:
                self._coreMouseButtonDown(button)
            else:
                self._coreMouseButtonUp(button)
        except Exception:
            return
        self._feedMacroEngine(stroke, False, False)

    def _registerSynthesizedScrollFeedback(self, direction: str, stroke) -> None:
        # Scroll has no held state — a momentary trigger-check only (see
        # _coreScroll), not forced into the held-key down/up model.
        try:
            self._coreScroll(direction)
        except Exception:
            return
        self._feedMacroEngine(stroke, False, False)

    def _feedMacroEngine(self, stroke, is_kb: bool, is_e0: bool) -> None:
        """Route a remap-synthesized stroke into the macro engine's own
        trigger detection — but only when a recording isn't in progress.
        The real physical source stroke is already fed to handleStroke()
        unconditionally from the listen loop (see _listenLoop), which is
        what recording is meant to capture; also feeding the synthesized
        target stroke while recording would double up every remapped
        key-press into two recorded actions."""
        if not self._macroEngine:
            return
        if self._macroEngine.isRecording:
            return
        self._macroEngine.handleStroke(stroke, is_kb, is_e0)

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
            weapon["strength_y"] = max(1, min(99, weapon.get("strength_y", 5) + direction))
            newStrength = weapon["strength_y"]
        if self._strengthCallback:
            self._strengthCallback(newStrength)

    def _strengthHoldLoop(self, direction: int, stop_event: threading.Event):
        if stop_event.wait(0.5):
            return
        while not stop_event.wait(0.25):
            self._applyStrength(direction)
