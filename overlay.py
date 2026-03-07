import tkinter as tk
from tkinter import ttk
import threading
import interception
from recoil import scancodeLabel

MOUSE_KEYS = {"mouse_left", "mouse_right", "mouse_middle"}

KEY_LABELS = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
}

MOUSE_BUTTON_FLAGS = {
    "mouse_left":   (interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_LEFT_BUTTON_UP),
    "mouse_right":  (interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_RIGHT_BUTTON_UP),
    "mouse_middle": (interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_DOWN,
                     interception.MouseButtonFlag.MOUSE_MIDDLE_BUTTON_UP),
}


def keyLabel(key: str) -> str:
    return KEY_LABELS.get(key, key.upper())


def comboLabel(keys: list) -> str:
    return " + ".join(keyLabel(k) for k in keys) if keys else "None"


class Overlay:
    def __init__(self, settings: dict, engine, onSettingsChanged):
        self._settings = settings
        self._engine = engine
        self._onSettingsChanged = onSettingsChanged
        self._capturing = False

        self._buildWindow()

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _buildWindow(self):
        self._root = tk.Tk()
        self._root.title("R9Tools")
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.92)
        self._root.resizable(False, False)
        self._root.configure(bg="#1e1e1e")

        s = self._settings["recoil"]

        # --- Header / toggle ---
        header = tk.Frame(self._root, bg="#1e1e1e")
        header.pack(fill="x", padx=10, pady=(10, 2))

        tk.Label(header, text="Recoil Compensation", fg="#ffffff",
                 bg="#1e1e1e", font=("Segoe UI", 10, "bold")).pack(side="left")

        tk.Button(
            header, text="✕", command=self._root.destroy,
            bg="#1e1e1e", fg="#ff6666", relief="flat",
            font=("Segoe UI", 10, "bold"), padx=4
        ).pack(side="right")

        self._enabledVar = tk.BooleanVar(value=s["enabled"])
        tk.Checkbutton(
            header, variable=self._enabledVar, command=self._onToggle,
            bg="#1e1e1e", activebackground="#1e1e1e",
            selectcolor="#333333", fg="#aaffaa"
        ).pack(side="right")

        # --- Status label ---
        self._statusVar = tk.StringVar(value=self._statusText())
        tk.Label(self._root, textvariable=self._statusVar,
                 fg="#888888", bg="#1e1e1e", font=("Segoe UI", 8)).pack(padx=10, anchor="w")

        ttk.Separator(self._root, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # --- Strength Y slider ---
        self._syVar = tk.IntVar(value=s["strength_y"])
        self._buildSlider("Pull Strength (px)", self._syVar, 1, 30, self._onSyChange)

        # --- Interval slider ---
        self._intervalVar = tk.IntVar(value=s["interval_ms"])
        self._buildSlider("Interval (ms)", self._intervalVar, 1, 50, self._onIntervalChange)

        ttk.Separator(self._root, orient="horizontal").pack(fill="x", padx=8, pady=6)

        # --- Trigger keybind ---
        kbFrame = tk.Frame(self._root, bg="#1e1e1e")
        kbFrame.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(kbFrame, text="Trigger:", fg="#ffffff",
                 bg="#1e1e1e", font=("Segoe UI", 9)).pack(side="left")

        self._keybindVar = tk.StringVar(value=comboLabel(s["trigger_keys"]))
        self._keybindBtn = tk.Button(
            kbFrame, textvariable=self._keybindVar,
            command=self._startCapture,
            bg="#333333", fg="#ffffff", relief="flat",
            font=("Segoe UI", 9), padx=8
        )
        self._keybindBtn.pack(side="right")

        # --- Toggle keybind ---
        togFrame = tk.Frame(self._root, bg="#1e1e1e")
        togFrame.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(togFrame, text="Toggle:", fg="#ffffff",
                 bg="#1e1e1e", font=("Segoe UI", 9)).pack(side="left")

        self._toggleKeyVar = tk.StringVar(value=scancodeLabel(s.get("toggle_key", 68)))
        self._toggleKeyBtn = tk.Button(
            togFrame, textvariable=self._toggleKeyVar,
            command=self._startToggleCapture,
            bg="#333333", fg="#ffffff", relief="flat",
            font=("Segoe UI", 9), padx=8
        )
        self._toggleKeyBtn.pack(side="right")

        self._pollStatus()

    def _buildSlider(self, label: str, var: tk.IntVar, from_: int, to: int, command):
        frame = tk.Frame(self._root, bg="#1e1e1e")
        frame.pack(fill="x", padx=10, pady=2)

        tk.Label(frame, text=label, fg="#cccccc", bg="#1e1e1e",
                 font=("Segoe UI", 9), width=18, anchor="w").pack(side="left")

        tk.Label(frame, textvariable=var, fg="#ffffff",
                 bg="#1e1e1e", font=("Segoe UI", 9), width=3).pack(side="right")

        tk.Scale(frame, variable=var, from_=from_, to=to, orient="horizontal",
                 command=lambda _: command(),
                 bg="#1e1e1e", fg="#cccccc", troughcolor="#333333",
                 highlightthickness=0, showvalue=False, length=160).pack(side="right")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def setEnabled(self, state: bool):
        """Called from engine toggle callback (runs on non-tkinter thread)."""
        self._root.after(0, self._applyEnabled, state)

    def _applyEnabled(self, state: bool):
        self._settings["recoil"]["enabled"] = state
        self._enabledVar.set(state)
        self._onSettingsChanged(self._settings)

    def _onToggle(self):
        self._settings["recoil"]["enabled"] = self._enabledVar.get()
        self._onSettingsChanged(self._settings)

    def _onSyChange(self):
        self._settings["recoil"]["strength_y"] = self._syVar.get()
        self._onSettingsChanged(self._settings)

    def _onIntervalChange(self):
        self._settings["recoil"]["interval_ms"] = self._intervalVar.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Combo keybind capture
    # ------------------------------------------------------------------

    def _startCapture(self):
        if self._capturing:
            return
        self._capturing = True
        self._keybindVar.set("Hold keys...")
        self._keybindBtn.config(fg="#ffff88")
        threading.Thread(target=self._captureThread, daemon=True).start()

    def _captureThread(self):
        inter = interception.Interception()
        inter.set_filter(
            inter.is_mouse,
            interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL
        )
        inter.set_filter(
            inter.is_keyboard,
            interception.FilterKeyFlag.FILTER_KEY_DOWN | interception.FilterKeyFlag.FILTER_KEY_UP
        )

        held: set = set()
        seen: list = []

        while self._capturing:
            deviceIdx = inter.await_input(100)
            if deviceIdx is None:
                continue

            device = inter._devices[deviceIdx]
            stroke = device.receive()
            if stroke is None:
                continue

            device.send(stroke)

            if isinstance(stroke, interception.MouseStroke):
                for key, (downFlag, upFlag) in MOUSE_BUTTON_FLAGS.items():
                    if stroke.button_flags & downFlag:
                        held.add(key)
                        if key not in seen:
                            seen.append(key)
                            self._root.after(0, lambda s=list(seen): self._keybindVar.set(
                                comboLabel(s) + " ..."
                            ))
                    elif stroke.button_flags & upFlag:
                        held.discard(key)

            elif isinstance(stroke, interception.KeyStroke):
                key = _codeToName(stroke.code)
                if key:
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(key)
                        if key not in seen:
                            seen.append(key)
                            self._root.after(0, lambda s=list(seen): self._keybindVar.set(
                                comboLabel(s) + " ..."
                            ))
                    else:
                        held.discard(key)

            if seen and not held:
                break

        combo = seen if seen else self._settings["recoil"]["trigger_keys"]
        self._settings["recoil"]["trigger_keys"] = combo
        self._capturing = False
        self._root.after(0, lambda: self._finishCapture(combo))

    def _finishCapture(self, combo: list):
        self._keybindVar.set(comboLabel(combo))
        self._keybindBtn.config(fg="#ffffff")
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Toggle key capture (single key only)
    # ------------------------------------------------------------------

    def _startToggleCapture(self):
        if self._capturing:
            return
        self._capturing = True
        self._toggleKeyVar.set("Press a key...")
        self._toggleKeyBtn.config(fg="#ffff88")
        threading.Thread(target=self._toggleCaptureThread, daemon=True).start()

    def _toggleCaptureThread(self):
        inter = interception.Interception()
        inter.set_filter(
            inter.is_keyboard,
            interception.FilterKeyFlag.FILTER_KEY_DOWN | interception.FilterKeyFlag.FILTER_KEY_UP
        )

        newKey: int | None = None
        while self._capturing:
            deviceIdx = inter.await_input(100)
            if deviceIdx is None:
                continue

            device = inter._devices[deviceIdx]
            stroke = device.receive()
            if stroke is None:
                continue

            device.send(stroke)

            if isinstance(stroke, interception.KeyStroke):
                if stroke.flags & interception.KeyFlag.KEY_UP:
                    newKey = stroke.code
                    break

        self._capturing = False
        if newKey is not None:
            self._settings["recoil"]["toggle_key"] = newKey
            self._root.after(0, lambda k=newKey: self._finishToggleCapture(k))

    def _finishToggleCapture(self, key: int):
        self._toggleKeyVar.set(scancodeLabel(key))
        self._toggleKeyBtn.config(fg="#ffffff")
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    def _statusText(self) -> str:
        return "ON" if self._settings["recoil"]["enabled"] else "OFF"

    def _pollStatus(self):
        if self._engine.isActive and self._settings["recoil"]["enabled"]:
            self._statusVar.set("ACTIVE")
        else:
            self._statusVar.set(self._statusText())
        self._root.after(100, self._pollStatus)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        self._root.mainloop()


def _codeToName(code: int) -> str | None:
    for name, val in vars(interception._keycodes).items():
        if isinstance(val, int) and val == code:
            return name
    return None
