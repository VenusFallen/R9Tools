import tkinter as tk
from tkinter import ttk
import threading
import interception

import theme
from panels.base import Panel
from recoil import scancodeLabel


class RecoilPanel(Panel):
    def __init__(self, root: tk.Tk, settings: dict, engine, onSettingsChanged):
        super().__init__(root)
        self._settings         = settings
        self._engine           = engine
        self._onSettingsChanged = onSettingsChanged
        self._capturing        = False
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        s = self._settings["recoil"]

        tk.Label(self._frame, text="Recoil Compensation",
                 fg=theme.ACCENT, bg=theme.PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        statusRow = tk.Frame(self._frame, bg=theme.PANEL_BG)
        statusRow.pack(anchor="w", padx=10, pady=(0, 4))
        self._statusDot = tk.Canvas(statusRow, width=8, height=8,
                                    bg=theme.PANEL_BG, highlightthickness=0)
        self._statusDot.pack(side="left", padx=(0, 5))
        self._statusVar = tk.StringVar(value="OFF")
        self._statusLabel = tk.Label(statusRow, textvariable=self._statusVar,
                                     fg=theme.DIM, bg=theme.PANEL_BG,
                                     font=("Segoe UI", 8))
        self._statusLabel.pack(side="left")
        self._drawStatusDot(theme.DIM)

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

        card = theme.buildCard(self._frame)
        self._syVar = tk.IntVar(value=s["strength_y"])
        theme.buildPlusMinusRow(card, "Pull Strength (px)", self._syVar, 1, 30, self._onSyChange)

        kbRow = tk.Frame(card, bg=theme.CARD_BG)
        kbRow.pack(fill="x", padx=10, pady=(3, 6))
        tk.Label(kbRow, text="Trigger:", fg=theme.LABEL_FG, bg=theme.CARD_BG,
                 font=("Segoe UI", 9), width=16, anchor="w").pack(side="left")
        self._keybindVar = tk.StringVar(value=theme.comboLabel(s["trigger_keys"]))
        self._keybindBtn = tk.Button(kbRow, textvariable=self._keybindVar,
                                     command=self._startCapture,
                                     bg=theme.BTN_BG, fg=theme.BTN_FG, relief="flat",
                                     font=("Segoe UI", 9), padx=8, cursor="hand2")
        self._keybindBtn.pack(side="right")
        theme.addHoverEffect(self._keybindBtn)

        self._pollStatus()

    # ------------------------------------------------------------------
    # Show / hide / reload
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        s = settings["recoil"]
        self._settings["recoil"].update(s)
        self._syVar.set(s["strength_y"])
        self._keybindVar.set(theme.comboLabel(s["trigger_keys"]))

    # ------------------------------------------------------------------
    # Engine callback
    # ------------------------------------------------------------------

    def updateStrength(self, value: int):
        self._syVar.set(value)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onSyChange(self):
        self._settings["recoil"]["strength_y"] = self._syVar.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    def _drawStatusDot(self, color: str):
        self._statusDot.delete("all")
        self._statusDot.create_oval(1, 1, 7, 7, fill=color, outline="")

    def _pollStatus(self):
        try:
            if self._engine.isActive and self._settings["recoil"]["enabled"]:
                self._statusVar.set("ACTIVE")
                self._drawStatusDot("#44ff88")
                self._statusLabel.config(fg="#44ff88")
            elif self._settings["recoil"]["enabled"]:
                self._statusVar.set("ON")
                self._drawStatusDot("#ffaa00")
                self._statusLabel.config(fg="#ffaa00")
            else:
                self._statusVar.set("OFF")
                self._drawStatusDot(theme.DIM)
                self._statusLabel.config(fg=theme.DIM)
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
        self._engine.setSuspendHotkeys(True)
        self._keybindVar.set("Hold keys...")
        self._keybindBtn.config(fg=theme.ACTIVE_FG)
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
                    for key, (downFlag, upFlag) in theme.MOUSE_BUTTON_FLAGS.items():
                        if stroke.button_flags & downFlag:
                            held.add(key)
                            if key not in seen:
                                seen.append(key)
                                self._root.after(0, lambda s=list(seen):
                                    self._keybindVar.set(theme.comboLabel(s) + " ..."))
                        elif stroke.button_flags & upFlag:
                            held.discard(key)

                elif isinstance(stroke, interception.KeyStroke):
                    isE0 = bool(stroke.flags & interception.KeyFlag.KEY_E0)
                    name = scancodeLabel(stroke.code, isE0)
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(name)
                        if name not in seen:
                            seen.append(name)
                            self._root.after(0, lambda s=list(seen):
                                self._keybindVar.set(theme.comboLabel(s) + " ..."))
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
        self._keybindVar.set(theme.comboLabel(combo))
        self._keybindBtn.config(fg=theme.BTN_FG)
        self._onSettingsChanged(self._settings)
