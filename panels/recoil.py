import tkinter as tk
from tkinter import ttk
import threading
import interception

from theme import (
    PANEL_BG, ACCENT, DIM, LABEL_FG, BTN_BG, BTN_FG, ACTIVE_FG,
    MOUSE_BUTTON_FLAGS, comboLabel, _codeToName, buildPlusMinusRow,
)
from panels.base import Panel


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
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        self._statusVar = tk.StringVar(value="OFF")
        tk.Label(self._frame, textvariable=self._statusVar,
                 fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=10, pady=(0, 4))

        ttk.Separator(self._frame, orient="horizontal").pack(fill="x", padx=8, pady=4)

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

    def reload(self, settings: dict):
        s = settings["recoil"]
        self._settings["recoil"].update(s)
        self._syVar.set(s["strength_y"])
        self._keybindVar.set(comboLabel(s["trigger_keys"]))

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
