import tkinter as tk
from tkinter import ttk

from theme import PANEL_BG, ACCENT, LABEL_FG, BTN_BG, BTN_FG, BG_TRANS, buildPlusMinusRow
from panels.base import Panel

try:
    import win32gui
    import win32process
    import psutil
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

_POLL_MS = 500   # foreground-window check interval


class CrosshairPanel(Panel):
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
        super().__init__(root)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._sw = root.winfo_screenwidth()
        self._sh = root.winfo_screenheight()
        self._buildCanvas()
        self._buildPanel()
        self._redraw()
        self._startWindowPoll()

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
    # Window filter polling
    # ------------------------------------------------------------------

    def _startWindowPoll(self):
        self._root.after(_POLL_MS, self._windowPoll)

    def _windowPoll(self):
        try:
            if self._settings["crosshair"]["enabled"]:
                if self._foregroundMatches():
                    self._placeCanvas()
                else:
                    self._canvas.place_forget()
        except tk.TclError:
            return   # window destroyed
        self._root.after(_POLL_MS, self._windowPoll)

    def _foregroundMatches(self) -> bool:
        """Returns True when the crosshair should be visible given the current window filter."""
        filter_name = self._settings.get("window_filter", "")
        if not filter_name:
            return True
        if not _WIN32_AVAILABLE:
            return True
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
            return name == filter_name.lower()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Panel — tab UI, shown/hidden with overlay menu
    # ------------------------------------------------------------------

    def _buildPanel(self):
        s = self._settings["crosshair"]

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
            if self._foregroundMatches():
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
        s["size"]         = self._sizeVar.get()
        s["thickness"]    = self._thickVar.get()
        s["gap"]          = self._gapVar.get()
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
