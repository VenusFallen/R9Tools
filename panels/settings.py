import tkinter as tk
from tkinter import ttk

import theme
from panels.base import Panel

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


class SettingsPanel(Panel):
    def __init__(self, root: tk.Tk, settings: dict, onSettingsChanged,
                 onCapture=None, onThemeChanged=None):
        super().__init__(root, right_anchor=True)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._onCapture         = onCapture
        self._onThemeChanged    = onThemeChanged
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        tk.Label(self._frame, text="Settings",
                 fg=theme.ACCENT, bg=theme.PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        # ---- Theme ----
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="Theme",
                 fg=theme.DIM, bg=theme.PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 2))

        themeRow = tk.Frame(self._frame, bg=theme.PANEL_BG)
        themeRow.pack(fill="x", padx=10, pady=(4, 4))
        tk.Label(themeRow, text="Color Theme:", fg=theme.LABEL_FG, bg=theme.PANEL_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._themeVar = tk.StringVar(value=self._settings.get("theme", "Dark"))
        self._themeBtns = {}
        for name in theme.THEME_NAMES:
            active = (name == self._themeVar.get())
            btn = tk.Button(themeRow, text=name.upper(),
                            command=lambda n=name: self._selectTheme(n),
                            bg=theme.BTN_BG if active else theme.BAR_BG,
                            fg=theme.ACCENT if active else theme.DIM,
                            relief="flat", font=("Segoe UI", 9), padx=8, cursor="hand2")
            btn.pack(side="left", padx=(0, 2))
            self._themeBtns[name] = btn

        # ---- Window Filter ----
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="Window Filter",
                 fg=theme.DIM, bg=theme.PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 2))
        tk.Label(self._frame,
                 text="Restrict active modules to the selected process.",
                 fg=theme.LABEL_FG, bg=theme.PANEL_BG,
                 font=("Segoe UI", 8),
                 wraplength=220, justify="left").pack(anchor="w", padx=12)

        filterRow = tk.Frame(self._frame, bg=theme.PANEL_BG)
        filterRow.pack(fill="x", padx=10, pady=(6, 4))

        self._windowVar = tk.StringVar(value=self._settings.get("window_filter", ""))
        self._windowCombo = ttk.Combobox(filterRow, textvariable=self._windowVar,
                                         width=20, state="readonly",
                                         font=("Segoe UI", 9))
        self._windowCombo.pack(side="left")
        self._windowCombo.bind("<<ComboboxSelected>>", self._onWindowChange)

        tk.Button(filterRow, text="↻", command=self._refreshProcesses,
                  bg=theme.BTN_BG, fg=theme.BTN_FG, relief="flat",
                  font=("Segoe UI", 9), padx=4, cursor="hand2").pack(side="left", padx=(4, 0))

        # ---- Hotkeys ----
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="Hotkeys",
                 fg=theme.ACCENT, bg=theme.PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(2, 2))

        # ---- Hotkeys — General ----
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="General",
                 fg=theme.DIM, bg=theme.PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 4))

        hk = self._settings["hotkeys"]

        self._overlayBtn = theme.KeybindButton(
            self._frame, "Menu Toggle:",
            binding=hk["overlay_toggle"],
            onChange=self._onOverlayToggleChange,
            onCapture=self._onCapture)

        self._quitBtn = theme.KeybindButton(
            self._frame, "Quit:",
            binding=hk["quit"],
            onChange=self._onQuitChange,
            onCapture=self._onCapture)

        # ---- Hotkeys — Recoil ----
        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)
        tk.Label(self._frame, text="Recoil",
                 fg=theme.DIM, bg=theme.PANEL_BG,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(2, 4))

        self._recoilBtn = theme.KeybindButton(
            self._frame, "Recoil Toggle:",
            binding=hk["recoil_toggle"],
            onChange=self._onRecoilToggleChange,
            onCapture=self._onCapture)

        self._strengthDownBtn = theme.KeybindButton(
            self._frame, "Strength -:",
            binding=hk["recoil_strength_down"],
            onChange=self._onStrengthDownChange,
            onCapture=self._onCapture)

        self._strengthUpBtn = theme.KeybindButton(
            self._frame, "Strength +:",
            binding=hk["recoil_strength_up"],
            onChange=self._onStrengthUpChange,
            onCapture=self._onCapture)

        tk.Frame(self._frame, bg=theme.PANEL_BG, height=7).pack()

        self._refreshProcesses()

    # ------------------------------------------------------------------
    # Process list
    # ------------------------------------------------------------------

    def _refreshProcesses(self):
        if not _PSUTIL_AVAILABLE:
            self._windowCombo["values"] = [""]
            return
        try:
            names = sorted(set(
                p.info["name"] for p in psutil.process_iter(["name"])
                if p.info["name"]
            ))
        except Exception:
            names = []
        current = self._windowVar.get()
        self._windowCombo["values"] = [""] + names
        if current not in names:
            self._windowVar.set("")

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _selectTheme(self, name: str):
        self._themeVar.set(name)
        for n, btn in self._themeBtns.items():
            active = (n == name)
            btn.config(bg=theme.BTN_BG if active else theme.BAR_BG,
                       fg=theme.ACCENT if active else theme.DIM)
        self._onThemeChange()

    def _onThemeChange(self):
        name = self._themeVar.get()
        self._settings["theme"] = name
        self._onSettingsChanged(self._settings)
        if self._onThemeChanged:
            self._onThemeChanged(name)

    def _onWindowChange(self, _=None):
        self._settings["window_filter"] = self._windowVar.get()
        self._onSettingsChanged(self._settings)

    def _onOverlayToggleChange(self, binding: dict):
        self._settings["hotkeys"]["overlay_toggle"] = binding
        self._onSettingsChanged(self._settings)

    def _onQuitChange(self, binding: dict):
        self._settings["hotkeys"]["quit"] = binding
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

    # ------------------------------------------------------------------
    # Reload (called on profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        self._settings["window_filter"] = settings.get("window_filter", "")
        filter_val = self._settings["window_filter"]
        if filter_val:
            current = list(self._windowCombo["values"])
            if filter_val not in current:
                self._windowCombo["values"] = ["", filter_val] + [v for v in current if v]
        self._windowVar.set(filter_val)

        new_theme = settings.get("theme", "Dark")
        self._themeVar.set(new_theme)
        for n, btn in self._themeBtns.items():
            active = (n == new_theme)
            btn.config(bg=theme.BTN_BG if active else theme.BAR_BG,
                       fg=theme.ACCENT if active else theme.DIM)

        hk = settings.get("hotkeys", {})
        self._settings["hotkeys"].update(hk)
        self._overlayBtn.setBinding(hk["overlay_toggle"])
        self._quitBtn.setBinding(hk["quit"])
        self._recoilBtn.setBinding(hk["recoil_toggle"])
        self._strengthDownBtn.setBinding(hk["recoil_strength_down"])
        self._strengthUpBtn.setBinding(hk["recoil_strength_up"])
