import tkinter as tk
from tkinter import ttk
import threading
import interception
import profiles as prof

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
TOPBAR_H    = 30
PANEL_OFFSET = 5
PANEL_Y      = TOPBAR_H + PANEL_OFFSET  # 35

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
BORDER_CLR = "#6B8E23"  # olive drab topbar border (restored after flash)

FLASH_SAVE   = "#44ff88"
FLASH_LOAD   = "#4488ff"
FLASH_DELETE = "#ff4444"
FLASH_MS     = 400

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
# Shared widget helpers
# ===========================================================================

def buildPlusMinusRow(frame, label: str, var: tk.IntVar,
                      minVal: int, maxVal: int, onChange):
    row = tk.Frame(frame, bg=PANEL_BG)
    row.pack(fill="x", padx=10, pady=3)

    tk.Label(row, text=label, fg=LABEL_FG, bg=PANEL_BG,
             font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")

    def adjust(delta):
        var.set(max(minVal, min(maxVal, var.get() + delta)))
        onChange()

    tk.Button(row, text="−", command=lambda: adjust(-1),
              bg=BTN_BG, fg=BTN_FG, relief="flat",
              font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(0, 1))

    entry = tk.Entry(row, textvariable=var, width=4,
                     font=("Segoe UI", 9), justify="center",
                     bg="#333333", fg=BTN_FG, relief="flat",
                     state="readonly", readonlybackground="#333333",
                     cursor="xterm", insertbackground=BTN_FG)

    def onClickEntry(_):
        entry.config(state="normal")
        entry.select_range(0, "end")

    def onCommit(_=None):
        try:
            val = max(minVal, min(maxVal, int(entry.get())))
            var.set(val)
            onChange()
        except ValueError:
            entry.delete(0, "end")
            entry.insert(0, str(var.get()))
        entry.config(state="readonly")

    entry.bind("<Button-1>", onClickEntry)
    entry.bind("<Return>",   onCommit)
    entry.bind("<FocusOut>", onCommit)
    entry.pack(side="left", padx=1)

    tk.Button(row, text="+", command=lambda: adjust(1),
              bg=BTN_BG, fg=BTN_FG, relief="flat",
              font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(1, 0))


class KeybindButton:
    """Reusable single-key capture widget (keyboard only).

    Renders a label + button showing the current binding. On click, starts an
    interception capture thread that waits for a key release, then calls
    `onChange({"code": int, "e0": bool})`.
    """

    def __init__(self, frame, label: str, binding: dict, onChange, onCapture=None):
        self._binding   = dict(binding)   # {"code": int, "e0": bool}
        self._onChange  = onChange
        self._onCapture = onCapture       # callable(bool) — True=start, False=end
        self._capturing = False

        row = tk.Frame(frame, bg=PANEL_BG)
        row.pack(fill="x", padx=10, pady=3)
        tk.Label(row, text=label, fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._var = tk.StringVar(value=self._bindingLabel())
        self._btn = tk.Button(row, textvariable=self._var,
                              command=self._startCapture,
                              bg=BTN_BG, fg=BTN_FG, relief="flat",
                              font=("Segoe UI", 9), padx=8, cursor="hand2")
        self._btn.pack(side="right")

    # ------------------------------------------------------------------

    def setBinding(self, binding: dict):
        self._binding = dict(binding)
        self._var.set(self._bindingLabel())

    def _bindingLabel(self) -> str:
        from recoil import scancodeLabel
        return scancodeLabel(self._binding["code"], self._binding["e0"])

    def _startCapture(self):
        if self._capturing:
            return
        self._capturing = True
        if self._onCapture:
            self._onCapture(True)
        self._var.set("Press a key...")
        self._btn.config(fg=ACTIVE_FG)
        threading.Thread(target=self._captureThread, daemon=True).start()

    def _captureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)

        newBinding: dict | None = None
        try:
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
                        newBinding = {
                            "code": stroke.code,
                            "e0":   bool(stroke.flags & interception.KeyFlag.KEY_E0),
                        }
                        break
        finally:
            self._capturing = False
            if self._onCapture:
                self._onCapture(False)

        if newBinding is not None:
            self._binding = newBinding
            label = self._bindingLabel()
            self._btn.winfo_toplevel().after(0, lambda: self._finish(label))
            self._onChange(newBinding)

    def _finish(self, label: str):
        self._var.set(label)
        self._btn.config(fg=BTN_FG)


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
        self._border = tk.Frame(self._root, bg="#EE82EE")
        self._frame = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

        tk.Label(self._frame, text="Recoil Compensation",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        self._statusVar = tk.StringVar(value="OFF")
        tk.Label(self._frame, textvariable=self._statusVar,
                 fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 4))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Strength / interval
        self._syVar = tk.IntVar(value=s["strength_y"])
        buildPlusMinusRow(self._frame, "Pull Strength (px)", self._syVar, 1, 30, self._onSyChange)

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Trigger keybind (combo: mouse + keyboard)
        kbRow = tk.Frame(self._frame, bg=PANEL_BG)
        kbRow.pack(fill="x", padx=10, pady=(3, 10))
        tk.Label(kbRow, text="Trigger:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._keybindVar = tk.StringVar(value=comboLabel(s["trigger_keys"]))
        self._keybindBtn = tk.Button(kbRow, textvariable=self._keybindVar,
                                     command=self._startCapture,
                                     bg=BTN_BG, fg=BTN_FG, relief="flat",
                                     font=("Segoe UI", 9), padx=8, cursor="hand2")
        self._keybindBtn.pack(side="right")

        self._pollStatus()

    # ------------------------------------------------------------------
    # Show / hide / reload
    # ------------------------------------------------------------------

    def show(self):
        self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()

    def reload(self, settings: dict):
        s = settings["recoil"]
        self._settings["recoil"].update(s)
        self._syVar.set(s["strength_y"])
        self._keybindVar.set(comboLabel(s["trigger_keys"]))

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def updateStrength(self, value: int):
        self._syVar.set(value)

    def _onSyChange(self):
        self._settings["recoil"]["strength_y"] = self._syVar.get()
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
    # Trigger keybind capture (combo: mouse + keyboard)
    # ------------------------------------------------------------------

    def _startCapture(self):
        if self._capturing:
            return
        self._capturing = True
        self._engine.setSuspendHotkeys(True)
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

        try:
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
        finally:
            self._capturing = False
            self._engine.setSuspendHotkeys(False)

        combo = seen if seen else self._settings["recoil"]["trigger_keys"]
        self._settings["recoil"]["trigger_keys"] = combo
        self._root.after(0, lambda: self._finishCapture(combo))

    def _finishCapture(self, combo: list):
        self._keybindVar.set(comboLabel(combo))
        self._keybindBtn.config(fg=BTN_FG)
        self._onSettingsChanged(self._settings)


# ===========================================================================
# ProfilesPanel
# ===========================================================================

class ProfilesPanel:
    def __init__(self, root: tk.Tk, profileData: dict,
                 onLoad, onSave, onDelete):
        self._root = root
        self._profileData = profileData
        self._onLoad = onLoad
        self._onSave = onSave
        self._onDelete = onDelete
        self._build()

    def _build(self):
        # 1px violet border via wrapper frame
        self._border = tk.Frame(self._root, bg="#EE82EE")
        self._frame = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

        # Title
        tk.Label(self._frame, text="Profiles",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Active profile label
        tk.Label(self._frame, text="Active Profile:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10)

        # Combobox + quick-save button
        comboRow = tk.Frame(self._frame, bg=PANEL_BG)
        comboRow.pack(fill="x", padx=10, pady=(2, 8))

        self._combo = ttk.Combobox(comboRow, state="readonly",
                                    font=("Segoe UI", 9), width=18)
        self._combo.pack(side="left", padx=(0, 4))
        self._refreshCombo()
        self._combo.bind("<<ComboboxSelected>>", self._onSelect)

        self._quickSaveBtn = tk.Button(comboRow, text="Save",
                                       command=self._onQuickSave,
                                       bg=BTN_BG, fg=BTN_FG, relief="flat",
                                       font=("Segoe UI", 9), padx=6,
                                       cursor="hand2")
        self._quickSaveBtn.pack(side="left")
        self._updateQuickSaveBtn()

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Name field label
        tk.Label(self._frame, text="Profile Name:", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(4, 2))

        # Name entry + Save + Delete
        actionRow = tk.Frame(self._frame, bg=PANEL_BG)
        actionRow.pack(fill="x", padx=10, pady=(0, 10))

        self._nameVar = tk.StringVar()
        tk.Entry(actionRow, textvariable=self._nameVar,
                 font=("Segoe UI", 9), bg="#333333", fg=BTN_FG,
                 relief="flat", insertbackground=BTN_FG,
                 width=14).pack(side="left", padx=(0, 4))

        tk.Button(actionRow, text="Save",
                  command=self._onSaveClick,
                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                  font=("Segoe UI", 9), padx=6,
                  cursor="hand2").pack(side="left", padx=(0, 2))

        tk.Button(actionRow, text="Delete",
                  command=self._onDeleteClick,
                  bg=BTN_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9), padx=6,
                  cursor="hand2").pack(side="left")

    # ------------------------------------------------------------------
    # Combo management
    # ------------------------------------------------------------------

    def refreshCombo(self):
        names = prof.profileNames(self._profileData)
        self._combo["values"] = names
        active = self._profileData["active"]
        self._combo.set(active if active in names else names[0])
        self._updateQuickSaveBtn()

    def _updateQuickSaveBtn(self):
        if not hasattr(self, "_quickSaveBtn"):
            return
        is_default = self._combo.get() == prof.DEFAULT_NAME
        self._quickSaveBtn.config(state="disabled" if is_default else "normal",
                                  fg=DIM if is_default else BTN_FG)

    # internal alias used during build before the public name is needed
    _refreshCombo = refreshCombo

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onSelect(self, _=None):
        name = self._combo.get()
        if name:
            self._onLoad(name)
        self._updateQuickSaveBtn()

    def _onQuickSave(self):
        name = self._combo.get()
        if name:
            self._onSave(name)

    def _onSaveClick(self):
        name = self._nameVar.get().strip()
        if name:
            self._onSave(name)

    def _onDeleteClick(self):
        name = self._nameVar.get().strip()
        if name:
            self._onDelete(name)

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def show(self):
        self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()


# ===========================================================================
# CrosshairPanel
# ===========================================================================

class CrosshairPanel:
    COLORS = {
        "green":  "#00ff00",
        "red":    "#ff0000",
        "white":  "#ffffff",
        "pink":   "#ff1493",
        "yellow": "#ffff00",
    }
    STYLES     = ["Dot", "Cross", "Dot + Cross", "Circle", "Circle + Dot"]
    STYLE_KEYS = ["dot", "cross", "dot_cross", "circle", "circle_dot"]
    CANVAS_HALF = 100   # 200×200 canvas; crosshair drawn at (100, 100)

    def __init__(self, root: tk.Tk, settings: dict, onSettingsChanged):
        self._root             = root
        self._settings         = settings
        self._onSettingsChanged = onSettingsChanged
        self._sw = root.winfo_screenwidth()
        self._sh = root.winfo_screenheight()
        self._buildCanvas()
        self._buildPanel()
        self._redraw()

    # ------------------------------------------------------------------
    # Canvas — always-on-top crosshair, independent of menu visibility
    # ------------------------------------------------------------------

    def _buildCanvas(self):
        size = self.CANVAS_HALF * 2
        self._canvas = tk.Canvas(
            self._root, width=size, height=size,
            bg=BG_TRANS, highlightthickness=0)
        self._placeCanvas()
        if not self._settings["crosshair"]["enabled"]:
            self._canvas.place_forget()

    def _placeCanvas(self):
        x = self._sw // 2 - self.CANVAS_HALF
        y = self._sh // 2 - self.CANVAS_HALF
        self._canvas.place(x=x, y=y)

    def _redraw(self):
        self._canvas.delete("all")
        s = self._settings["crosshair"]
        if not s["enabled"]:
            return

        cx = cy  = self.CANVAS_HALF
        color    = self.COLORS.get(s["color"], "#ffffff")
        size     = s["size"]
        thick    = s["thickness"]
        gap      = s["gap"]
        style    = s["style"]
        OUT      = "#000000"
        out_sz   = s["outline_size"]
        out_w    = thick + out_sz * 2

        def draw_dot(r):
            if out_sz > 0:
                self._canvas.create_oval(
                    cx-r-1, cy-r-1, cx+r+1, cy+r+1, fill=OUT, outline="")
            self._canvas.create_oval(
                cx-r, cy-r, cx+r, cy+r, fill=color, outline="")

        def draw_cross():
            arms = [
                (cx, cy - gap - size, cx, cy - gap),
                (cx, cy + gap,        cx, cy + gap + size),
                (cx - gap - size, cy, cx - gap,        cy),
                (cx + gap,        cy, cx + gap + size,  cy),
            ]
            if out_sz > 0:
                for x1, y1, x2, y2 in arms:
                    self._canvas.create_line(
                        x1, y1, x2, y2,
                        fill=OUT, width=out_w, capstyle=tk.ROUND)
            for x1, y1, x2, y2 in arms:
                self._canvas.create_line(
                    x1, y1, x2, y2,
                    fill=color, width=thick, capstyle=tk.ROUND)

        def draw_circle():
            r = size
            if out_sz > 0:
                self._canvas.create_oval(
                    cx-r-1, cy-r-1, cx+r+1, cy+r+1,
                    outline=OUT, width=out_w, fill="")
            self._canvas.create_oval(
                cx-r, cy-r, cx+r, cy+r,
                outline=color, width=thick, fill="")

        if style == "dot":
            draw_dot(max(1, size // 2))
        elif style == "cross":
            draw_cross()
        elif style == "dot_cross":
            draw_cross()
            draw_dot(max(1, thick))
        elif style == "circle":
            draw_circle()
        elif style == "circle_dot":
            draw_circle()
            draw_dot(max(1, thick))

    # ------------------------------------------------------------------
    # Panel — tab UI, shown/hidden with overlay menu
    # ------------------------------------------------------------------

    def _buildPanel(self):
        s = self._settings["crosshair"]
        self._border = tk.Frame(self._root, bg="#EE82EE")
        self._frame  = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

        tk.Label(self._frame, text="Crosshair",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Enabled
        self._enabledVar = tk.BooleanVar(value=s["enabled"])
        enableRow = tk.Frame(self._frame, bg=PANEL_BG)
        enableRow.pack(fill="x", padx=10, pady=3)
        tk.Label(enableRow, text="Enabled", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        tk.Checkbutton(enableRow, variable=self._enabledVar, command=self._onToggle,
                       bg=PANEL_BG, activebackground=PANEL_BG,
                       selectcolor=BTN_BG, fg="#aaffaa").pack(side="left")

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Style
        styleRow = tk.Frame(self._frame, bg=PANEL_BG)
        styleRow.pack(fill="x", padx=10, pady=3)
        tk.Label(styleRow, text="Style", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._styleVar = tk.StringVar(value=self._keyToLabel(s["style"]))
        styleBox = ttk.Combobox(styleRow, textvariable=self._styleVar,
                                values=self.STYLES, state="readonly",
                                font=("Segoe UI", 9), width=14)
        styleBox.pack(side="left")
        styleBox.bind("<<ComboboxSelected>>", lambda _: self._onStyleChange())

        # Color
        colorRow = tk.Frame(self._frame, bg=PANEL_BG)
        colorRow.pack(fill="x", padx=10, pady=3)
        tk.Label(colorRow, text="Color", fg=LABEL_FG, bg=PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._colorVar = tk.StringVar(value=s["color"].capitalize())
        colorBox = ttk.Combobox(colorRow, textvariable=self._colorVar,
                                values=[c.capitalize() for c in self.COLORS],
                                state="readonly", font=("Segoe UI", 9), width=14)
        colorBox.pack(side="left")
        colorBox.bind("<<ComboboxSelected>>", lambda _: self._onColorChange())

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        # Size
        self._sizeVar = tk.IntVar(value=s["size"])
        buildPlusMinusRow(self._frame, "Size", self._sizeVar, 1, 30, self._onParamChange)

        # Thickness
        self._thickVar = tk.IntVar(value=s["thickness"])
        buildPlusMinusRow(self._frame, "Thickness", self._thickVar, 1, 10, self._onParamChange)

        # Gap
        self._gapVar = tk.IntVar(value=s["gap"])
        buildPlusMinusRow(self._frame, "Gap", self._gapVar, 0, 20, self._onParamChange)

        # Outline Size (0 = no outline)
        self._outlineSizeVar = tk.IntVar(value=s["outline_size"])
        buildPlusMinusRow(self._frame, "Outline Size", self._outlineSizeVar, 0, 5, self._onParamChange)
        tk.Frame(self._frame, bg=PANEL_BG, height=7).pack()

    # ------------------------------------------------------------------
    # Show / hide (panel only — canvas is unaffected)
    # ------------------------------------------------------------------

    def show(self):
        self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()

    # ------------------------------------------------------------------
    # Reload (called on profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        s = settings["crosshair"]
        self._settings["crosshair"].update(s)
        self._enabledVar.set(False)
        self._styleVar.set(self._keyToLabel(s["style"]))
        self._colorVar.set(s["color"].capitalize())
        self._sizeVar.set(s["size"])
        self._thickVar.set(s["thickness"])
        self._gapVar.set(s["gap"])
        self._outlineSizeVar.set(s["outline_size"])
        self._settings["crosshair"]["enabled"] = False
        self._canvas.place_forget()
        self._redraw()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onToggle(self):
        enabled = self._enabledVar.get()
        self._settings["crosshair"]["enabled"] = enabled
        if enabled:
            self._placeCanvas()
            self._redraw()
        else:
            self._canvas.place_forget()
        self._onSettingsChanged(self._settings)

    def _onStyleChange(self):
        self._settings["crosshair"]["style"] = self._labelToKey(self._styleVar.get())
        self._redraw()
        self._onSettingsChanged(self._settings)

    def _onColorChange(self):
        self._settings["crosshair"]["color"] = self._colorVar.get().lower()
        self._redraw()
        self._onSettingsChanged(self._settings)

    def _onParamChange(self):
        s = self._settings["crosshair"]
        s["size"]      = self._sizeVar.get()
        s["thickness"] = self._thickVar.get()
        s["gap"]         = self._gapVar.get()
        s["outline_size"] = self._outlineSizeVar.get()
        self._redraw()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _keyToLabel(self, key: str) -> str:
        try:
            return self.STYLES[self.STYLE_KEYS.index(key)]
        except ValueError:
            return self.STYLES[1]   # default: Cross

    def _labelToKey(self, label: str) -> str:
        try:
            return self.STYLE_KEYS[self.STYLES.index(label)]
        except ValueError:
            return self.STYLE_KEYS[1]  # default: cross


# ===========================================================================
# HotkeyPanel
# ===========================================================================

class HotkeyPanel:
    def __init__(self, root: tk.Tk, settings: dict, onSettingsChanged, onCapture=None):
        self._root             = root
        self._settings         = settings
        self._onSettingsChanged = onSettingsChanged
        self._onCapture        = onCapture
        self._build()

    def _build(self):
        self._border = tk.Frame(self._root, bg="#EE82EE")
        self._frame  = tk.Frame(self._border, bg=PANEL_BG)
        self._frame.pack(padx=1, pady=1)

        tk.Label(self._frame, text="Hotkeys",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        # --- General section ---
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="General",
                 fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 4))

        hk = self._settings["hotkeys"]
        self._overlayBtn = KeybindButton(
            self._frame, "Menu Toggle:",
            binding=hk["overlay_toggle"],
            onChange=self._onOverlayToggleChange,
            onCapture=self._onCapture)

        # --- Recoil section ---
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="Recoil",
                 fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 4))

        self._recoilBtn = KeybindButton(
            self._frame, "Recoil Toggle:",
            binding=hk["recoil_toggle"],
            onChange=self._onRecoilToggleChange,
            onCapture=self._onCapture)

        self._strengthDownBtn = KeybindButton(
            self._frame, "Strength -:",
            binding=hk["recoil_strength_down"],
            onChange=self._onStrengthDownChange,
            onCapture=self._onCapture)

        self._strengthUpBtn = KeybindButton(
            self._frame, "Strength +:",
            binding=hk["recoil_strength_up"],
            onChange=self._onStrengthUpChange,
            onCapture=self._onCapture)

        tk.Frame(self._frame, bg=PANEL_BG, height=7).pack()

    # ------------------------------------------------------------------
    # Show / hide / reload
    # ------------------------------------------------------------------

    def show(self):
        self._border.place(x=0, y=PANEL_Y)

    def hide(self):
        self._border.place_forget()

    def reload(self, settings: dict):
        hk = settings["hotkeys"]
        self._settings["hotkeys"].update(hk)
        self._overlayBtn.setBinding(hk["overlay_toggle"])
        self._recoilBtn.setBinding(hk["recoil_toggle"])
        self._strengthDownBtn.setBinding(hk["recoil_strength_down"])
        self._strengthUpBtn.setBinding(hk["recoil_strength_up"])

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onOverlayToggleChange(self, binding: dict):
        self._settings["hotkeys"]["overlay_toggle"] = binding
        self._onSettingsChanged(self._settings)

    def _onRecoilToggleChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_toggle"] = binding
        self._onSettingsChanged(self._settings)

    def _onStrengthDownChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_strength_down"] = binding
        self._onSettingsChanged(self._settings)

    def _onStrengthUpChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_strength_up"] = binding
        self._onSettingsChanged(self._settings)


# ===========================================================================
# Overlay
# ===========================================================================

class Overlay:
    def __init__(self, settings: dict, profileData: dict, engine, onSettingsChanged):
        self._settings = settings
        self._profileData = profileData
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

        self._root.configure(bg=BG_TRANS)
        self._root.attributes("-transparentcolor", BG_TRANS)

        # Style ttk dropdowns to match dark theme
        style = ttk.Style(self._root)
        style.theme_use("default")
        style.configure("TCombobox",
                         fieldbackground="#333333", background=BTN_BG,
                         foreground=BTN_FG, arrowcolor=BTN_FG,
                         selectbackground="#444444", selectforeground=BTN_FG)
        style.map("TCombobox", fieldbackground=[("readonly", "#333333")])
        self._root.option_add("*TCombobox*Listbox.background", "#333333")
        self._root.option_add("*TCombobox*Listbox.foreground", BTN_FG)
        self._root.option_add("*TCombobox*Listbox.selectBackground", "#555555")
        self._root.option_add("*TCombobox*Listbox.selectForeground", BTN_FG)

        self._buildTopBar()
        self._buildPanels()
        self._buildStrengthIndicator(sw, sh)
        self._hideOverlay()

    def _buildTopBar(self):
        self._topBar = tk.Frame(self._root, bg=BAR_BG)
        self._topBarBorder = tk.Frame(self._root, bg=BORDER_CLR)
        self._tabButtons: list[tk.Button] = []

        self._addTab("RECOIL", 0)
        self._addTab("CROSSHAIR", 1)
        self._addTab("HOTKEYS", 2)
        self._addTab("PROFILES", 3)

        # QUIT pinned to the right
        tk.Button(self._topBar, text="QUIT",
                  command=self._root.destroy,
                  bg=BAR_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=12,
                  activebackground=BTN_BG, activeforeground="#ff6666",
                  cursor="hand2").pack(side="right")

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
        self._recoilPanel = RecoilPanel(
            self._root, self._settings, self._engine, self._onSettingsChanged)
        self._crosshairPanel = CrosshairPanel(
            self._root, self._settings, self._onSettingsChanged)
        self._hotkeyPanel = HotkeyPanel(
            self._root, self._settings, self._onSettingsChanged,
            onCapture=self._engine.setSuspendHotkeys)
        self._profilesPanel = ProfilesPanel(
            self._root, self._profileData,
            onLoad=self._onProfileLoad,
            onSave=self._onProfileSave,
            onDelete=self._onProfileDelete)

        self._panels = [self._recoilPanel, self._crosshairPanel,
                        self._hotkeyPanel, self._profilesPanel]
        self._tabButtons[0].config(fg=ACCENT, bg=BTN_BG)

    def _buildStrengthIndicator(self, sw: int, sh: int):
        W, H = 35, 24
        x_off = int(sw * 0.02)
        y_off = int(sh * 0.03)
        self._siX = sw // 2 - x_off - W // 2
        self._siY = sh // 2 - y_off - H // 2
        self._siCanvas = tk.Canvas(self._root, width=W, height=H,
                                   bg=BG_TRANS, highlightthickness=0)
        self._siHideId  = None
        self._siFont    = ("Segoe UI", 12, "bold")
        self._siCx      = W // 2
        self._siCy      = H // 2

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
    # Topbar border flash
    # ------------------------------------------------------------------

    def flashBorder(self, color: str):
        try:
            self._topBarBorder.config(bg=color)
            self._root.after(FLASH_MS, lambda: self._topBarBorder.config(bg=BORDER_CLR))
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Profile callbacks
    # ------------------------------------------------------------------

    def _onProfileLoad(self, name: str):
        settings = prof.loadProfile(self._profileData, name)
        if settings is None:
            return
        # Update live settings dict in place
        for key in settings:
            self._settings[key] = settings[key]
        self._engine.updateSettings(self._settings)
        self._recoilPanel.reload(self._settings)
        self._crosshairPanel.reload(self._settings)
        self._hotkeyPanel.reload(self._settings)
        self._profilesPanel.refreshCombo()
        self.flashBorder(FLASH_LOAD)

    def _onProfileSave(self, name: str):
        success = prof.saveProfile(self._profileData, name, self._settings)
        if success:
            self._profilesPanel.refreshCombo()
            self.flashBorder(FLASH_SAVE)

    def _onProfileDelete(self, name: str):
        wasActive = (name == self._profileData.get("active"))
        success = prof.deleteProfile(self._profileData, name)
        if success:
            self._profilesPanel.refreshCombo()
            self.flashBorder(FLASH_DELETE)
            # If we deleted the active profile, reload Default
            if wasActive:
                settings = prof.loadProfile(self._profileData, prof.DEFAULT_NAME)
                if settings:
                    for key in settings:
                        self._settings[key] = settings[key]
                    self._engine.updateSettings(self._settings)
                    self._recoilPanel.reload(self._settings)
                    self._crosshairPanel.reload(self._settings)
                    self._hotkeyPanel.reload(self._settings)

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def setEnabled(self, state: bool):
        """Called from engine thread when toggle key is pressed."""
        self._root.after(0, self._applyEnabled, state)

    def onStrengthChanged(self, value: int):
        """Called from engine thread when strength hotkey is pressed."""
        self._root.after(0, self._recoilPanel.updateStrength, value)
        self._root.after(0, self._showStrengthIndicator, value)

    def _showStrengthIndicator(self, value: int):
        c = self._siCanvas
        cx, cy = self._siCx, self._siCy
        c.delete("all")
        text = str(value)
        for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
            c.create_text(cx + dx, cy + dy, text=text,
                          fill="#000000", font=self._siFont, anchor="center")
        c.create_text(cx, cy, text=text,
                      fill="#ffffff", font=self._siFont, anchor="center")
        c.place(x=self._siX, y=self._siY)
        if self._siHideId is not None:
            self._root.after_cancel(self._siHideId)
        self._siHideId = self._root.after(500, self._hideStrengthIndicator)

    def _hideStrengthIndicator(self):
        self._siCanvas.place_forget()
        self._siHideId = None

    def _applyEnabled(self, state: bool):
        self._settings["recoil"]["enabled"] = state
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        self._root.mainloop()
