import tkinter as tk
import threading
import interception

from theme import PANEL_BG, BTN_BG, BTN_FG, LABEL_FG, ACCENT, DIM, ACTIVE_FG
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":    "Mouse4",
    "mouse_x2":    "Mouse5",
}


def _inputLabel(inp: dict) -> str:
    t = inp.get("type", "")
    if t == "key":
        return scancodeLabel(inp["code"], inp.get("e0", False))
    if t == "mouse":
        return _MOUSE_DISPLAY.get(inp.get("button", ""), inp.get("button", "?"))
    if t == "scroll":
        return "Scroll Up" if inp.get("direction") == "up" else "Scroll Down"
    return "?"


class RemapperPanel(Panel):
    def __init__(self, root: tk.Tk, settings: dict, onSettingsChanged):
        super().__init__(root)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._capturing         = False
        self._pendingFrom       = None
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        tk.Label(self._frame, text="Button Remapper",
                 fg=ACCENT, bg=PANEL_BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))

        # Enabled checkbox
        ctrlRow = tk.Frame(self._frame, bg=PANEL_BG)
        ctrlRow.pack(fill="x", padx=10, pady=(2, 4))

        self._enabledVar = tk.BooleanVar(value=False)   # always starts off
        tk.Checkbutton(ctrlRow, text="Enabled", variable=self._enabledVar,
                       command=self._onEnabledChange,
                       bg=PANEL_BG, fg=LABEL_FG, selectcolor=BTN_BG,
                       activebackground=PANEL_BG, activeforeground=ACCENT,
                       font=("Segoe UI", 9)).pack(side="left")

        # Column headers
        hdrRow = tk.Frame(self._frame, bg=PANEL_BG)
        hdrRow.pack(fill="x", padx=10, pady=(4, 1))
        tk.Label(hdrRow, text="FROM", fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8, "bold"), width=10, anchor="w").pack(side="left")
        tk.Label(hdrRow, text="→", fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8)).pack(side="left", padx=4)
        tk.Label(hdrRow, text="TO", fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 8, "bold"), width=10, anchor="w").pack(side="left")

        # Mappings container
        self._mapFrame = tk.Frame(self._frame, bg=PANEL_BG)
        self._mapFrame.pack(fill="x", padx=10)

        # Capture status label (hidden unless capturing)
        self._captureLabel = tk.Label(self._frame, text="",
                                      fg=ACTIVE_FG, bg=PANEL_BG,
                                      font=("Segoe UI", 9, "italic"))

        # Add mapping button
        tk.Button(self._frame, text="+ Add Mapping", command=self._startAddMapping,
                  bg=BTN_BG, fg=BTN_FG, relief="flat",
                  font=("Segoe UI", 9), padx=8, cursor="hand2").pack(anchor="w", padx=10, pady=(6, 8))

        self._refreshMappingRows()

    # ------------------------------------------------------------------
    # Mapping rows
    # ------------------------------------------------------------------

    def _refreshMappingRows(self):
        for widget in self._mapFrame.winfo_children():
            widget.destroy()

        mappings = self._settings["remapper"]["mappings"]
        for i, mapping in enumerate(mappings):
            self._addMappingRow(i, mapping)

    def _addMappingRow(self, index: int, mapping: dict):
        row = tk.Frame(self._mapFrame, bg=PANEL_BG)
        row.pack(fill="x", pady=1)

        fromBtn = tk.Button(row, text=_inputLabel(mapping["from"]),
                            width=9, command=lambda i=index: self._editFrom(i),
                            bg=BTN_BG, fg=BTN_FG, relief="flat",
                            font=("Segoe UI", 9), cursor="hand2")
        fromBtn.pack(side="left")

        tk.Label(row, text="→", fg=DIM, bg=PANEL_BG,
                 font=("Segoe UI", 9)).pack(side="left", padx=4)

        toBtn = tk.Button(row, text=_inputLabel(mapping["to"]),
                          width=9, command=lambda i=index: self._editTo(i),
                          bg=BTN_BG, fg=BTN_FG, relief="flat",
                          font=("Segoe UI", 9), cursor="hand2")
        toBtn.pack(side="left")

        tk.Button(row, text="×", command=lambda i=index: self._deleteMapping(i),
                  bg=BTN_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9), padx=4, cursor="hand2").pack(side="right")

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _startAddMapping(self):
        if self._capturing:
            return
        self._pendingFrom = None
        self._startCapture("FROM: Press any key or button...", self._onFromCaptured,
                           allow_scroll=True)

    def _editFrom(self, index: int):
        if self._capturing:
            return
        self._startCapture("FROM: Press any key or button...",
                           lambda inp: self._onEditFromCaptured(index, inp),
                           allow_scroll=True)

    def _editTo(self, index: int):
        if self._capturing:
            return
        self._startCapture("TO: Press any key or button (or scroll)...",
                           lambda inp: self._onEditToCaptured(index, inp),
                           allow_scroll=True)

    def _onFromCaptured(self, inp: dict):
        if inp is None:
            return
        if self._isProtected(inp):
            self._captureLabel.config(text="That key is protected and cannot be remapped.")
            self._captureLabel.pack(padx=10, pady=(0, 4))
            self._root.after(2000, self._captureLabel.pack_forget)
            return
        self._pendingFrom = inp
        self._startCapture("TO: Press any key or button (or scroll)...",
                           self._onToCaptured, allow_scroll=True)

    def _onToCaptured(self, inp: dict):
        if inp is None or self._pendingFrom is None:
            return
        mappings = self._settings["remapper"]["mappings"]
        mappings.append({"from": self._pendingFrom, "to": inp})
        self._pendingFrom = None
        self._refreshMappingRows()
        self._onSettingsChanged(self._settings)

    def _onEditFromCaptured(self, index: int, inp: dict):
        if inp is None:
            return
        if self._isProtected(inp):
            return
        mappings = self._settings["remapper"]["mappings"]
        if 0 <= index < len(mappings):
            mappings[index]["from"] = inp
            self._refreshMappingRows()
            self._onSettingsChanged(self._settings)

    def _onEditToCaptured(self, index: int, inp: dict):
        if inp is None:
            return
        mappings = self._settings["remapper"]["mappings"]
        if 0 <= index < len(mappings):
            mappings[index]["to"] = inp
            self._refreshMappingRows()
            self._onSettingsChanged(self._settings)

    def _deleteMapping(self, index: int):
        mappings = self._settings["remapper"]["mappings"]
        if 0 <= index < len(mappings):
            del mappings[index]
        self._refreshMappingRows()
        self._onSettingsChanged(self._settings)

    def _isProtected(self, inp: dict) -> bool:
        if inp.get("type") != "key":
            return False
        hotkeys = self._settings.get("hotkeys", {})
        for name in ("overlay_toggle", "quit"):
            bind = hotkeys.get(name, {})
            if inp["code"] == bind.get("code") and inp.get("e0", False) == bind.get("e0", False):
                return True
        return False

    # ------------------------------------------------------------------
    # Input capture
    # ------------------------------------------------------------------

    def _startCapture(self, prompt: str, callback, allow_scroll: bool = False):
        self._capturing = True
        self._captureLabel.config(text=prompt)
        self._captureLabel.pack(padx=10, pady=(0, 4))
        threading.Thread(target=self._captureThread,
                         args=(callback, allow_scroll), daemon=True).start()

    def _captureThread(self, callback, allow_scroll: bool):
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)
        inter.set_filter(inter.is_mouse, interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)

        result = None
        try:
            while result is None:
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
                        result = {
                            "type": "key",
                            "code": stroke.code,
                            "e0":   bool(stroke.flags & interception.KeyFlag.KEY_E0),
                        }

                elif isinstance(stroke, interception.MouseStroke):
                    if allow_scroll and stroke.button_flags & _SCROLL_WHEEL_FLAG:
                        delta = stroke.button_data
                        if delta > 32767:
                            delta -= 65536
                        result = {
                            "type":      "scroll",
                            "direction": "up" if delta > 0 else "down",
                        }
                    else:
                        for name, (downFlag, upFlag) in MOUSE_BUTTON_FLAGS.items():
                            if stroke.button_flags & upFlag:
                                result = {"type": "mouse", "button": name}
                                break
        finally:
            self._capturing = False

        self._root.after(0, self._captureLabel.pack_forget)
        self._root.after(0, lambda: callback(result))

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _onEnabledChange(self):
        self._settings["remapper"]["enabled"] = self._enabledVar.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Reload (called on profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        self._settings["remapper"].update(settings.get("remapper", {}))
        self._enabledVar.set(False)
        self._settings["remapper"]["enabled"] = False
        self._refreshMappingRows()
