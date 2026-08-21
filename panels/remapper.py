import threading
import interception

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

import theme
import keybind_conflicts
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel

_FROM_PROMPT = "FROM: Press any key or button..."

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
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

    # Fired from capture thread when input is received — no args.
    # Callback + result stored as instance attrs before emit.
    _captureDone = Signal()

    def __init__(self, parent, settings: dict, onSettingsChanged):
        super().__init__(parent)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._capturing         = False
        self._pendingFrom       = None
        self._pendingCallback   = None
        self._pendingResult     = None
        # Prompt for the capture currently in progress — used to redraw
        # the "still listening" prompt after a rejection flash message if
        # a re-armed capture is still running.
        self._activeCapturePrompt = None

        self._captureDone.connect(self._onCaptureDone)
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        title = QLabel("Button Remapper")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        # Enabled toggle
        ctrlRow = QFrame()
        cl = QHBoxLayout(ctrlRow)
        cl.setContentsMargins(10, 2, 10, 4)
        cl.setSpacing(6)
        cl.addWidget(QLabel("Enabled"))
        self._enabledSwitch = theme.ToggleSwitch(
            ctrlRow, value=False, command=self._onEnabledChange)
        cl.addWidget(self._enabledSwitch)
        cl.addStretch()
        self._layout.addWidget(ctrlRow)

        # Column headers
        hdrRow = QFrame()
        hl = QHBoxLayout(hdrRow)
        hl.setContentsMargins(10, 4, 10, 1)
        hl.setSpacing(4)
        from_lbl = QLabel("FROM")
        from_lbl.setStyleSheet(f"color: {theme.DIM}; font: bold 8pt 'Segoe UI';")
        from_lbl.setFixedWidth(72)
        arr_lbl = QLabel("→")
        arr_lbl.setStyleSheet(f"color: {theme.DIM};")
        to_lbl = QLabel("TO")
        to_lbl.setStyleSheet(f"color: {theme.DIM}; font: bold 8pt 'Segoe UI';")
        hl.addWidget(from_lbl)
        hl.addWidget(arr_lbl)
        hl.addWidget(to_lbl)
        hl.addStretch()
        self._layout.addWidget(hdrRow)

        # Mapping rows container
        self._mapWidget = QWidget()
        self._mapWidget.setStyleSheet(f"background-color: {theme.PANEL_BG};")
        self._mapLayout = QVBoxLayout(self._mapWidget)
        self._mapLayout.setContentsMargins(10, 0, 10, 0)
        self._mapLayout.setSpacing(1)
        self._layout.addWidget(self._mapWidget)

        # Capture status label (hidden unless capturing)
        self._captureLabel = QLabel("")
        self._captureLabel.setStyleSheet(
            f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 4px 10px;")
        self._captureLabel.setVisible(False)
        self._layout.addWidget(self._captureLabel)

        # Add mapping button
        addBtn = QPushButton("+ Add Mapping")
        addBtn.setStyleSheet(
            f"text-align: left; padding: 3px 10px;"
            f" margin: 6px 10px 8px 10px;")
        addBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        addBtn.clicked.connect(self._startAddMapping)
        self._layout.addWidget(addBtn)

        self._refreshMappingRows()

    # ------------------------------------------------------------------
    # Mapping rows
    # ------------------------------------------------------------------

    def _refreshMappingRows(self):
        while self._mapLayout.count():
            item = self._mapLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, mapping in enumerate(self._settings["remapper"]["mappings"]):
            self._addMappingRow(mapping)

    def _addMappingRow(self, mapping: dict):
        row = QFrame(self._mapWidget)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        fromBtn = QPushButton(_inputLabel(mapping["from"]), row)
        fromBtn.setFixedWidth(72)
        fromBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        fromBtn.clicked.connect(lambda _, m=mapping: self._editFrom(m))
        rl.addWidget(fromBtn)

        arr = QLabel("→", row)
        arr.setStyleSheet(f"color: {theme.DIM};")
        rl.addWidget(arr)

        toBtn = QPushButton(_inputLabel(mapping["to"]), row)
        toBtn.setFixedWidth(72)
        toBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        toBtn.clicked.connect(lambda _, m=mapping: self._editTo(m))
        rl.addWidget(toBtn)

        delBtn = QPushButton("×", row)
        delBtn.setFixedWidth(24)
        delBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        delBtn.setStyleSheet(
            f"QPushButton {{ color: #ff6666; background-color: {theme.BTN_BG}; border: none; }}"
            f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")
        delBtn.clicked.connect(lambda _, m=mapping: self._deleteMapping(m))
        rl.addWidget(delBtn)

        rl.addStretch()
        self._mapLayout.addWidget(row)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _startAddMapping(self):
        if self._capturing:
            return
        self._pendingFrom = None
        self._startCapture(_FROM_PROMPT, self._onFromCaptured, allow_scroll=True)

    def _editFrom(self, mapping: dict):
        if self._capturing:
            return
        self._startCapture(_FROM_PROMPT,
                           lambda inp: self._onEditFromCaptured(mapping, inp),
                           allow_scroll=True)

    def _editTo(self, mapping: dict):
        if self._capturing:
            return
        self._startCapture("TO: Press any key or button (or scroll)...",
                           lambda inp: self._onEditToCaptured(mapping, inp),
                           allow_scroll=True)

    def _onFromCaptured(self, inp: dict):
        if inp is None:
            return
        # Conflict check doubles as the "protected hotkeys can't be remap
        # sources" rule: Menu Toggle / Quit always have a concrete binding
        # in settings["hotkeys"] and are registered as ordinary conflict
        # sources (see keybind_conflicts.iterBindingSources), so capturing
        # either of their keys here is reported the same way any other
        # collision would be — one consistent code path instead of a
        # bespoke protected-key check.
        conflict = keybind_conflicts.findConflict(self._settings, inp)
        if conflict:
            # Re-arm immediately so the very next key press is captured
            # and checked again, instead of dropping out of capture mode.
            self._flashConflict(
                conflict,
                rearm=lambda: self._startCapture(
                    _FROM_PROMPT, self._onFromCaptured,
                    allow_scroll=True, reprompt=False))
            return
        self._pendingFrom = inp
        self._startCapture("TO: Press any key or button (or scroll)...",
                           self._onToCaptured, allow_scroll=True)

    def _onToCaptured(self, inp: dict):
        if inp is None or self._pendingFrom is None:
            return
        self._settings["remapper"]["mappings"].append(
            {"from": self._pendingFrom, "to": inp})
        self._pendingFrom = None
        self._refreshMappingRows()
        self._onSettingsChanged(self._settings)

    def _onEditFromCaptured(self, mapping: dict, inp: dict):
        if inp is None:
            return
        conflict = keybind_conflicts.findConflict(
            self._settings, inp, exclude_id=f"remap_from:{id(mapping)}")
        if conflict:
            # Re-arm immediately so the very next key press is captured
            # and checked again, instead of dropping out of capture mode.
            self._flashConflict(
                conflict,
                rearm=lambda: self._startCapture(
                    _FROM_PROMPT,
                    lambda inp: self._onEditFromCaptured(mapping, inp),
                    allow_scroll=True, reprompt=False))
            return
        mappings = self._settings["remapper"]["mappings"]
        if any(m is mapping for m in mappings):
            mapping["from"] = inp
            self._refreshMappingRows()
            self._onSettingsChanged(self._settings)

    def _flashConflict(self, conflict_with: str, rearm=None):
        """Transient hard-block message on the capture-prompt label — the
        captured binding is discarded and, if `rearm` is given, capture is
        re-armed immediately so the very next key press is captured and
        checked again (the prompt reappears once the flash message's time
        is up), instead of leaving the user to click FROM again."""
        self._captureLabel.setText(f"Already bound to {conflict_with}. Try again.")
        self._captureLabel.setStyleSheet(
            f"color: #ff6666; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 4px 10px;")
        self._captureLabel.setVisible(True)
        if rearm:
            rearm()
        QTimer.singleShot(2200, self._resetCaptureLabel)

    def _resetCaptureLabel(self):
        # If a re-armed capture is still running when this timer fires,
        # keep the prompt visible instead of hiding it — otherwise the
        # label would disappear while still silently listening for a key.
        if self._capturing and self._activeCapturePrompt:
            self._captureLabel.setText(self._activeCapturePrompt)
            self._captureLabel.setStyleSheet(
                f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
                f" padding: 0px 10px 4px 10px;")
            self._captureLabel.setVisible(True)
        else:
            self._captureLabel.setVisible(False)
            self._captureLabel.setStyleSheet(
                f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
                f" padding: 0px 10px 4px 10px;")

    def _onEditToCaptured(self, mapping: dict, inp: dict):
        if inp is None:
            return
        mappings = self._settings["remapper"]["mappings"]
        if any(m is mapping for m in mappings):
            mapping["to"] = inp
            self._refreshMappingRows()
            self._onSettingsChanged(self._settings)

    def _deleteMapping(self, mapping: dict):
        mappings = self._settings["remapper"]["mappings"]
        try:
            idx = next(i for i, m in enumerate(mappings) if m is mapping)
            del mappings[idx]
        except StopIteration:
            pass
        self._refreshMappingRows()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Input capture
    # ------------------------------------------------------------------

    def _startCapture(self, prompt: str, callback, allow_scroll: bool = False,
                       reprompt: bool = True):
        self._capturing           = True
        self._pendingCallback     = callback
        self._activeCapturePrompt = prompt
        if reprompt:
            # Skipped when re-arming right after a rejection flash — the
            # flash message is already showing and should stay visible for
            # its full duration instead of being immediately overwritten.
            self._captureLabel.setText(prompt)
            self._captureLabel.setStyleSheet(
                f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
                f" padding: 0px 10px 4px 10px;")
            self._captureLabel.setVisible(True)
        threading.Thread(target=self._captureThread,
                         args=(allow_scroll,), daemon=True).start()

    def _captureThread(self, allow_scroll: bool):
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard,
                         interception.FilterKeyFlag.FILTER_KEY_ALL)
        inter.set_filter(inter.is_mouse,
                         interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        result = None
        try:
            while result is None:
                idx = inter.await_input(100)
                if idx is None:
                    continue
                if idx >= len(inter._devices):
                    continue
                device = inter._devices[idx]
                stroke = device.receive()
                if stroke is None:
                    continue

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

        self._pendingResult = result
        self._captureDone.emit()

    @Slot()
    def _onCaptureDone(self):
        self._captureLabel.setVisible(False)
        cb     = self._pendingCallback
        result = self._pendingResult
        self._pendingCallback = None
        self._pendingResult   = None
        if cb:
            cb(result)

    # ------------------------------------------------------------------
    # Change handlers / reload
    # ------------------------------------------------------------------

    def _onEnabledChange(self):
        self._settings["remapper"]["enabled"] = self._enabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def reload(self, settings: dict):
        self._settings["remapper"].update(settings.get("remapper", {}))
        self._enabledSwitch.set(False)
        self._settings["remapper"]["enabled"] = False
        self._refreshMappingRows()
