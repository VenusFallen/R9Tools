"""
Shared UI constants, widget helpers, and reusable widgets.
Imported by all panel modules and by overlay.py.
"""
import tkinter as tk
from tkinter import ttk
import threading
import interception

# ---------------------------------------------------------------------------
# Layout constants (not theme-dependent)
# ---------------------------------------------------------------------------
TOPBAR_H     = 30
PANEL_OFFSET = 5
PANEL_Y      = TOPBAR_H + PANEL_OFFSET  # 35
PANEL_MIN_W  = 240

BG_TRANS   = "#000001"   # transparent / click-through (never changes)
BORDER_CLR = "#6B8E23"   # olive drab topbar border (never changes)

FLASH_SAVE   = "#44ff88"
FLASH_LOAD   = "#4488ff"
FLASH_DELETE = "#ff4444"
FLASH_MS     = 400

# ---------------------------------------------------------------------------
# Theme palettes
# ---------------------------------------------------------------------------
THEMES = {
    "Dark": {
        "BAR_BG":        "#141414",
        "PANEL_BG":      "#1e1e1e",
        "BTN_BG":        "#2d2d2d",
        "BTN_FG":        "#ffffff",
        "LABEL_FG":      "#cccccc",
        "ACCENT":        "#4a9eff",
        "DIM":           "#888888",
        "ACTIVE_FG":     "#ffff88",
        "ENTRY_BG":      "#333333",
        "PANEL_BORDER":  "#2a2a2a",
        "CARD_BG":       "#252525",
        "HOVER_BG":      "#3a3a3a",
        "TAB_HOVER_BG":  "#202020",
    },
    "Light": {
        "BAR_BG":        "#c4c8cc",
        "PANEL_BG":      "#f0f2f4",
        "BTN_BG":        "#b0b6bc",
        "BTN_FG":        "#0d0d0d",
        "LABEL_FG":      "#2a2a2a",
        "ACCENT":        "#1a6fd4",
        "DIM":           "#606060",
        "ACTIVE_FG":     "#004e99",
        "ENTRY_BG":      "#dde0e4",
        "PANEL_BORDER":  "#9a9ea2",
        "CARD_BG":       "#e6e8ea",
        "HOVER_BG":      "#c8ccd0",
        "TAB_HOVER_BG":  "#bbbfc3",
    },
}

THEME_NAMES = list(THEMES.keys())


def setTheme(name: str) -> None:
    """Update module-level color globals to the named theme palette."""
    palette = THEMES.get(name, THEMES["Dark"])
    g = globals()
    for key, val in palette.items():
        g[key] = val


# ---------------------------------------------------------------------------
# Active theme color globals — initialised to Dark
# (these are updated in-place by setTheme())
# ---------------------------------------------------------------------------
BAR_BG       = THEMES["Dark"]["BAR_BG"]
PANEL_BG     = THEMES["Dark"]["PANEL_BG"]
BTN_BG       = THEMES["Dark"]["BTN_BG"]
BTN_FG       = THEMES["Dark"]["BTN_FG"]
LABEL_FG     = THEMES["Dark"]["LABEL_FG"]
ACCENT       = THEMES["Dark"]["ACCENT"]
DIM          = THEMES["Dark"]["DIM"]
ACTIVE_FG    = THEMES["Dark"]["ACTIVE_FG"]
ENTRY_BG     = THEMES["Dark"]["ENTRY_BG"]
PANEL_BORDER = THEMES["Dark"]["PANEL_BORDER"]
CARD_BG      = THEMES["Dark"]["CARD_BG"]
HOVER_BG     = THEMES["Dark"]["HOVER_BG"]
TAB_HOVER_BG = THEMES["Dark"]["TAB_HOVER_BG"]

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
# Helper functions
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


def buildCard(parent) -> tk.Frame:
    """Grouped control container with CARD_BG background. Returns the frame to pack children into."""
    card = tk.Frame(parent, bg=CARD_BG)
    card.pack(fill="x", padx=8, pady=3)
    return card


def addTabHoverEffect(btn: tk.Button) -> None:
    """Hover highlight for inactive tab buttons only (skips active tab at BTN_BG)."""
    def on_enter(_):
        try:
            if btn.cget("bg") == BAR_BG:
                btn.config(bg=TAB_HOVER_BG)
        except tk.TclError:
            pass

    def on_leave(_):
        try:
            if btn.cget("bg") == TAB_HOVER_BG:
                btn.config(bg=BAR_BG)
        except tk.TclError:
            pass

    btn.bind("<Enter>", on_enter, add=True)
    btn.bind("<Leave>", on_leave, add=True)


def addHoverEffect(btn: tk.Button) -> None:
    """Add subtle HOVER_BG highlight on mouse-over. Restores original bg on leave."""
    orig_bg = btn.cget("bg")

    def on_enter(_):
        try:
            btn.config(bg=HOVER_BG)
        except tk.TclError:
            pass

    def on_leave(_):
        try:
            btn.config(bg=orig_bg)
        except tk.TclError:
            pass

    btn.bind("<Enter>", on_enter, add=True)
    btn.bind("<Leave>", on_leave, add=True)


# ---------------------------------------------------------------------------
# Shared widgets
# ---------------------------------------------------------------------------

def buildPlusMinusRow(frame, label: str, var: tk.IntVar,
                      minVal: int, maxVal: int, onChange):
    row = tk.Frame(frame, bg=PANEL_BG)
    row.pack(fill="x", padx=10, pady=3)

    tk.Label(row, text=label, fg=LABEL_FG, bg=PANEL_BG,
             font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")

    def adjust(delta):
        var.set(max(minVal, min(maxVal, var.get() + delta)))
        onChange()

    btn_minus = tk.Button(row, text="−", command=lambda: adjust(-1),
                          bg=BTN_BG, fg=BTN_FG, relief="flat",
                          font=("Segoe UI", 9), width=2, cursor="hand2")
    btn_minus.pack(side="left", padx=(0, 1))
    addHoverEffect(btn_minus)

    entry = tk.Entry(row, textvariable=var, width=4,
                     font=("Segoe UI", 9), justify="center",
                     bg=ENTRY_BG, fg=BTN_FG, relief="flat",
                     state="readonly", readonlybackground=ENTRY_BG,
                     cursor="xterm", insertbackground=BTN_FG)

    def onClickEntry(_):
        entry.config(state="normal")
        entry.select_range(0, "end")

    def onCommit(_=None):
        if entry.cget("state") == "readonly":
            return
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

    btn_plus = tk.Button(row, text="+", command=lambda: adjust(1),
                         bg=BTN_BG, fg=BTN_FG, relief="flat",
                         font=("Segoe UI", 9), width=2, cursor="hand2")
    btn_plus.pack(side="left", padx=(1, 0))
    addHoverEffect(btn_plus)


class ToggleSwitch:
    """
    Pill-shaped sliding toggle. Drop-in replacement for tk.Checkbutton.
    Accepts a tk.BooleanVar and an optional command callback.
    Syncs visually with programmatic var changes (via trace).
    Cancels in-progress animation on rapid clicks.
    """

    W     = 40    # canvas width
    H     = 20    # canvas height
    R     = 8     # knob radius
    PAD   = 2     # knob padding from pill edge
    STEPS = 8     # animation steps
    DELAY = 15    # ms per step  (~120ms total)

    def __init__(self, parent, variable: tk.BooleanVar, command=None):
        self._var     = variable
        self._command = command
        self._animId  = None
        self._knobX   = float(self._targetX(variable.get()))

        self._canvas = tk.Canvas(
            parent, width=self.W, height=self.H,
            bg=PANEL_BG, highlightthickness=0, cursor="hand2")
        self._canvas.bind("<Button-1>", self._onClick)
        self._canvas.bind("<Destroy>",  self._onDestroy)

        # Sync visual state with programmatic var changes
        self._traceName = variable.trace_add("write", self._onVarChange)

        self._draw()

    # ------------------------------------------------------------------
    # Public pack/grid/place passthrough
    # ------------------------------------------------------------------

    def pack(self, **kwargs):
        self._canvas.pack(**kwargs)

    def grid(self, **kwargs):
        self._canvas.grid(**kwargs)

    def place(self, **kwargs):
        self._canvas.place(**kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _targetX(self, value: bool) -> float:
        return self.W - self.R - self.PAD if value else self.R + self.PAD

    def _onClick(self, _=None):
        self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _onVarChange(self, *_):
        target = self._targetX(self._var.get())
        if abs(self._knobX - target) < 0.5:
            return
        self._startAnimation(target)

    def _startAnimation(self, target: float):
        # Cancel any in-progress animation before starting a new one
        if self._animId is not None:
            try:
                self._canvas.winfo_toplevel().after_cancel(self._animId)
            except Exception:
                pass
            self._animId = None

        step = (target - self._knobX) / self.STEPS
        self._animateStep(target, step, self.STEPS)

    def _animateStep(self, target: float, step: float, remaining: int):
        if remaining <= 0:
            self._knobX = target
            self._draw()
            self._animId = None
            return

        self._knobX += step
        self._draw()

        try:
            self._animId = self._canvas.winfo_toplevel().after(
                self.DELAY,
                lambda: self._animateStep(target, step, remaining - 1)
            )
        except tk.TclError:
            self._animId = None

    def _draw(self):
        try:
            c = self._canvas
            c.delete("all")

            track = ACCENT if self._var.get() else DIM
            knob  = PANEL_BG

            # Pill track: two end-caps + center rectangle
            c.create_oval(0,          0, self.H,          self.H, fill=track, outline="")
            c.create_oval(self.W - self.H, 0, self.W, self.H, fill=track, outline="")
            c.create_rectangle(self.H // 2, 0, self.W - self.H // 2, self.H,
                               fill=track, outline="")

            # Knob
            x = round(self._knobX)
            y = self.H // 2
            c.create_oval(x - self.R, y - self.R, x + self.R, y + self.R,
                          fill=knob, outline="")
        except tk.TclError:
            pass

    def _onDestroy(self, _=None):
        try:
            self._var.trace_remove("write", self._traceName)
        except Exception:
            pass
        if self._animId is not None:
            try:
                self._canvas.winfo_toplevel().after_cancel(self._animId)
            except Exception:
                pass


class KeybindButton:
    """Reusable single-key capture widget (keyboard only)."""

    def __init__(self, frame, label: str, binding: dict, onChange, onCapture=None):
        self._binding   = dict(binding)
        self._onChange  = onChange
        self._onCapture = onCapture
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
        addHoverEffect(self._btn)

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
            label    = self._bindingLabel()
            binding  = newBinding
            self._btn.winfo_toplevel().after(
                0, lambda: (self._finish(label), self._onChange(binding)))

    def _finish(self, label: str):
        self._var.set(label)
        self._btn.config(fg=BTN_FG)
