import tkinter as tk
from tkinter import ttk
import threading
import interception
from recoil import scancodeLabel

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
TOPBAR_H = 30
PANEL_OFFSET = 5
PANEL_Y = TOPBAR_H + PANEL_OFFSET  # 35

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
BG_TRANS  = "#000001"   # transparent / click-through color
BAR_BG    = "#141414"
PANEL_BG  = "#1e1e1e"
BTN_BG    = "#2d2d2d"
BTN_FG    = "#ffffff"
LABEL_FG  = "#cccccc"
ACCENT    = "#ffffff"
DIM       = "#888888"
ACTIVE_FG = "#ffff88"

# ---------------------------------------------------------------------------
# Input constants
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def keyLabel(key: str) -> str:
    return KEY_LABELS.get(key, key.upper())


def comboLabel(keys: list) -> str:
    return " + ".join(keyLabel(k) for k in keys) if keys else "None"


def _codeToName(code: int) -> str | None:
    for name, val in vars(interception._keycodes).items():
        if isinstance(val, int) and val == code:
            return name
    return None


# ===========================================================================
# RecoilPanel
# ===========================================================================

class RecoilPanel:
    def __init__(self, root: tk.Tk, settings: dict, engine, onSettingsChanged):
        self._root = root
        self._settings = settings
        self._engine = engine
        self._onSettingsChanged = onSettingsChanged
        self._capturing = False
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        s = self._settings["recoil"]
        # 1px violet border via wrapper frame
        self._border = tk.Frame(self._root, bg="#EE82EE")
        self._frame = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

        # Title
        tk.Label(self._frame, text="Recoil Compensation",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        # Status
        self._statusVar = tk.StringVar(value="OFF")
        tk.Label(self._frame, textvariable=self._statusVar,
                 fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 4))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Enabled toggle
        self._enabledVar = tk.BooleanVar(value=s["enabled"])
        enableRow = tk.Frame(self._frame, bg=PANEL_BG)
        enableRow.pack(fill="x", padx=10, pady=3)
        tk.Label(enableRow, text="Enabled", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        tk.Checkbutton(enableRow, variable=self._enabledVar, command=self._onToggle,
                       bg=PANEL_BG, activebackground=PANEL_BG,
                       selectcolor=BTN_BG, fg="#aaffaa").pack(side="left")

        # Strength Y
        self._syVar = tk.IntVar(value=s["strength_y"])
        self._buildPlusMinusRow("Pull Strength (px)", self._syVar, 1, 30, self._onSyChange)

        # Interval
        self._intervalVar = tk.IntVar(value=s["interval_ms"])
        self._buildPlusMinusRow("Interval (ms)", self._intervalVar, 1, 50, self._onIntervalChange)

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Trigger keybind
        kbRow = tk.Frame(self._frame, bg=PANEL_BG)
        kbRow.pack(fill="x", padx=10, pady=3)
        tk.Label(kbRow, text="Trigger:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._keybindVar = tk.StringVar(value=comboLabel(s["trigger_keys"]))
        self._keybindBtn = tk.Button(kbRow, textvariable=self._keybindVar,
                                     command=self._startCapture,
                                     bg=BTN_BG, fg=BTN_FG, relief="flat",
                                     font=("Segoe UI", 9), padx=8, cursor="hand2")
        self._keybindBtn.pack(side="right")

        # Toggle key
        togRow = tk.Frame(self._frame, bg=PANEL_BG)
        togRow.pack(fill="x", padx=10, pady=(3, 10))
        tk.Label(togRow, text="Toggle Key:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._toggleKeyVar = tk.StringVar(value=scancodeLabel(s.get("toggle_key", 68)))
        self._toggleKeyBtn = tk.Button(togRow, textvariable=self._toggleKeyVar,
                                       command=self._startToggleCapture,
                                       bg=BTN_BG, fg=BTN_FG, relief="flat",
                                       font=("Segoe UI", 9), padx=8, cursor="hand2")
        self._toggleKeyBtn.pack(side="right")

        self._pollStatus()

    def _buildPlusMinusRow(self, label: str, var: tk.IntVar,
                           minVal: int, maxVal: int, onChange):
        row = tk.Frame(self._frame, bg=PANEL_BG)
        row.pack(fill="x", padx=10, pady=3)

        tk.Label(row, text=label, fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")

        tk.Button(row, text="−",
                  command=lambda: self._adjustVar(var, -1, minVal, maxVal, onChange),
                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                  font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(0, 1))

        # Entry styled as a label; click to edit
        entry = tk.Entry(row, textvariable=var, width=4,
                         font=("Segoe UI", 9), justify="center",
                         bg="#333333", fg=BTN_FG, relief="flat",
                         state="readonly", readonlybackground="#333333",
                         cursor="xterm", insertbackground=BTN_FG)

        def onClickEntry(_, ent=entry):
            ent.config(state="normal")
            ent.select_range(0, "end")

        def onCommit(_=None, ent=entry, v=var, mn=minVal, mx=maxVal, cb=onChange):
            try:
                val = max(mn, min(mx, int(ent.get())))
                v.set(val)
                cb()
            except ValueError:
                ent.delete(0, "end")
                ent.insert(0, str(v.get()))
            ent.config(state="readonly")

        entry.bind("<Button-1>", onClickEntry)
        entry.bind("<Return>", onCommit)
        entry.bind("<FocusOut>", onCommit)
        entry.pack(side="left", padx=1)

        tk.Button(row, text="+",
                  command=lambda: self._adjustVar(var, 1, minVal, maxVal, onChange),
                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                  font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(1, 0))

    def _adjustVar(self, var: tk.IntVar, delta: int,
                   minVal: int, maxVal: int, onChange):
        var.set(max(minVal, min(maxVal, var.get() + delta)))
        onChange()

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def show(self):
        self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def refreshEnabled(self, state: bool):
        self._enabledVar.set(state)

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
    # Status polling
    # ------------------------------------------------------------------

    def _pollStatus(self):
        try:
            if self._engine.isActive and self._settings["recoil"]["enabled"]:
                self._statusVar.set("ACTIVE")
            elif self._settings["recoil"]["enabled"]:
                self._statusVar.set("ON")
            else:
                self._statusVar.set("OFF")
            self._root.after(100, self._pollStatus)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Trigger keybind capture
    # ------------------------------------------------------------------

    def _startCapture(self):
        if self._capturing:
            return
        self._capturing = True
        self._keybindVar.set("Hold keys...")
        self._keybindBtn.config(fg=ACTIVE_FG)
        threading.Thread(target=self._captureThread, daemon=True).start()

    def _captureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_mouse, interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        inter.set_filter(inter.is_keyboard,
                         interception.FilterKeyFlag.FILTER_KEY_DOWN |
                         interception.FilterKeyFlag.FILTER_KEY_UP)

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
                            self._root.after(0, lambda s=list(seen):
                                self._keybindVar.set(comboLabel(s) + " ..."))
                    elif stroke.button_flags & upFlag:
                        held.discard(key)

            elif isinstance(stroke, interception.KeyStroke):
                name = _codeToName(stroke.code)
                if name:
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(name)
                        if name not in seen:
                            seen.append(name)
                            self._root.after(0, lambda s=list(seen):
                                self._keybindVar.set(comboLabel(s) + " ..."))
                    else:
                        held.discard(name)

            if seen and not held:
                break

        combo = seen if seen else self._settings["recoil"]["trigger_keys"]
        self._settings["recoil"]["trigger_keys"] = combo
        self._capturing = False
        self._root.after(0, lambda: self._finishCapture(combo))

    def _finishCapture(self, combo: list):
        self._keybindVar.set(comboLabel(combo))
        self._keybindBtn.config(fg=BTN_FG)
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Toggle key capture
    # ------------------------------------------------------------------

    def _startToggleCapture(self):
        if self._capturing:
            return
        self._capturing = True
        self._toggleKeyVar.set("Press a key...")
        self._toggleKeyBtn.config(fg=ACTIVE_FG)
        threading.Thread(target=self._toggleCaptureThread, daemon=True).start()

    def _toggleCaptureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard,
                         interception.FilterKeyFlag.FILTER_KEY_DOWN |
                         interception.FilterKeyFlag.FILTER_KEY_UP)

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
        self._toggleKeyBtn.config(fg=BTN_FG)
        self._onSettingsChanged(self._settings)


# ===========================================================================
# Overlay
# ===========================================================================

class Overlay:
    def __init__(self, settings: dict, engine, onSettingsChanged):
        self._settings = settings
        self._engine = engine
        self._onSettingsChanged = onSettingsChanged
        self._visible = False
        self._activeTab = 0
        self._buildWindow()

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _buildWindow(self):
        self._root = tk.Tk()
        self._root.title("R9Tools")
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        self._root.geometry(f"{sw}x{sh}+0+0")

        # BG_TRANS pixels are invisible and click-through on Windows
        self._root.configure(bg=BG_TRANS)
        self._root.attributes("-transparentcolor", BG_TRANS)

        self._buildTopBar()
        self._buildPanels()

        # Start hidden
        self._hideOverlay()

    def _buildTopBar(self):
        self._topBar = tk.Frame(self._root, bg=BAR_BG)
        # 2px olive drab bottom border strip
        self._topBarBorder = tk.Frame(self._root, bg="#6B8E23")
        self._tabButtons: list[tk.Button] = []

        # Module tabs (left side)
        self._addTab("RECOIL", 0)

        # QUIT pinned to the right
        tk.Button(self._topBar, text="QUIT",
                  command=self._root.destroy,
                  bg=BAR_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=12,
                  activebackground=BTN_BG, activeforeground="#ff6666",
                  cursor="hand2").pack(side="right")

        # Arrow key navigation (only active when overlay is focused)
        self._root.bind("<Left>",  lambda _: self._shiftTab(-1))
        self._root.bind("<Right>", lambda _: self._shiftTab(1))

    def _addTab(self, label: str, index: int):
        btn = tk.Button(self._topBar, text=label,
                        command=lambda i=index: self._selectTab(i),
                        bg=BAR_BG, fg=DIM, relief="flat",
                        font=("Segoe UI", 9, "bold"), padx=12,
                        activebackground=BTN_BG, activeforeground=ACCENT,
                        cursor="hand2")
        btn.pack(side="left")
        self._tabButtons.append(btn)

    def _buildPanels(self):
        self._panels = [
            RecoilPanel(self._root, self._settings, self._engine, self._onSettingsChanged),
        ]
        # Initialise tab highlight without showing anything yet
        self._tabButtons[0].config(fg=ACCENT, bg=BTN_BG)

    def _selectTab(self, index: int):
        self._activeTab = index
        for i, btn in enumerate(self._tabButtons):
            btn.config(fg=ACCENT if i == index else DIM,
                       bg=BTN_BG if i == index else BAR_BG)
        for i, panel in enumerate(self._panels):
            if i == index:
                panel.show()
            else:
                panel.hide()

    def _shiftTab(self, direction: int):
        self._selectTab((self._activeTab + direction) % len(self._panels))

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def _showOverlay(self):
        if self._visible:
            return
        self._visible = True
        self._topBar.place(x=0, y=0, relwidth=1.0, height=TOPBAR_H)
        self._topBarBorder.place(x=0, y=TOPBAR_H, relwidth=1.0, height=2)
        self._selectTab(self._activeTab)
        self._root.focus_force()

    def _hideOverlay(self):
        self._visible = False
        self._topBar.place_forget()
        self._topBarBorder.place_forget()
        for panel in self._panels:
            panel.hide()

    def toggleOverlay(self):
        """Called from engine thread — schedules on tkinter thread."""
        self._root.after(0, self._toggleOverlay)

    def _toggleOverlay(self):
        if self._visible:
            self._hideOverlay()
        else:
            self._showOverlay()

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def setEnabled(self, state: bool):
        """Called from engine thread when toggle key is pressed."""
        self._root.after(0, self._applyEnabled, state)

    def _applyEnabled(self, state: bool):
        self._settings["recoil"]["enabled"] = state
        self._onSettingsChanged(self._settings)
        self._panels[0].refreshEnabled(state)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        self._root.mainloop()
