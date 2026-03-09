"""
Shared UI constants, widget helpers, and the KeybindButton widget.
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
        "BAR_BG":   "#141414",
        "PANEL_BG": "#1e1e1e",
        "BTN_BG":   "#2d2d2d",
        "BTN_FG":   "#ffffff",
        "LABEL_FG": "#cccccc",
        "ACCENT":   "#ffffff",
        "DIM":      "#888888",
        "ACTIVE_FG":"#ffff88",
        "ENTRY_BG": "#333333",
    },
    "Light": {
        "BAR_BG":   "#c4c8cc",
        "PANEL_BG": "#f0f2f4",
        "BTN_BG":   "#b0b6bc",
        "BTN_FG":   "#0d0d0d",
        "LABEL_FG": "#2a2a2a",
        "ACCENT":   "#0d0d0d",
        "DIM":      "#606060",
        "ACTIVE_FG":"#004e99",
        "ENTRY_BG": "#dde0e4",
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
BAR_BG    = THEMES["Dark"]["BAR_BG"]
PANEL_BG  = THEMES["Dark"]["PANEL_BG"]
BTN_BG    = THEMES["Dark"]["BTN_BG"]
BTN_FG    = THEMES["Dark"]["BTN_FG"]
LABEL_FG  = THEMES["Dark"]["LABEL_FG"]
ACCENT    = THEMES["Dark"]["ACCENT"]
DIM       = THEMES["Dark"]["DIM"]
ACTIVE_FG = THEMES["Dark"]["ACTIVE_FG"]
ENTRY_BG  = THEMES["Dark"]["ENTRY_BG"]

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

    tk.Button(row, text="−", command=lambda: adjust(-1),
              bg=BTN_BG, fg=BTN_FG, relief="flat",
              font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(0, 1))

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

    tk.Button(row, text="+", command=lambda: adjust(1),
              bg=BTN_BG, fg=BTN_FG, relief="flat",
              font=("Segoe UI", 9), width=2, cursor="hand2").pack(side="left", padx=(1, 0))


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
