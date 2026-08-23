import threading
import interception

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

import theme
import keybind_conflicts
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel
from interception_bringup import bringUpInterception, destroyInterception

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
        self._pendingFailed     = False

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
        conflict = keybind_conflicts.findConflict(self._settings, inp)
        if conflict and self._blockProtectedHotkey(_inputLabel(inp), conflict):
            return
        if conflict and not self._confirmConflict(_inputLabel(inp), conflict):
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
        if conflict and self._blockProtectedHotkey(_inputLabel(inp), conflict):
            return
        if conflict and not self._confirmConflict(_inputLabel(inp), conflict):
            return
        mappings = self._settings["remapper"]["mappings"]
        if any(m is mapping for m in mappings):
            mapping["from"] = inp
            self._refreshMappingRows()
            self._onSettingsChanged(self._settings)

    def _blockProtectedHotkey(self, new_label: str, conflict_with: str) -> bool:
        """Menu Toggle and Quit are a hard, non-overridable mutual
        exclusion with remapper FROM sources — unlike every other conflict
        type (warn-and-confirm via _confirmConflict), a capture landing on
        either of them is always reverted, no dialog choice offered.
        Returns True if `conflict_with` names one of those two protected
        hotkeys (and a hard-block message has already been shown), False
        if the caller should fall through to the normal confirm flow."""
        if conflict_with not in keybind_conflicts.PROTECTED_REMAP_LABELS:
            return False
        reserved_for = conflict_with.split(": ", 1)[-1]
        QMessageBox.warning(
            self, "Reserved hotkey",
            f"{new_label} is reserved for {reserved_for} and cannot be "
            f"used as a remap source.",
        )
        return True

    def _confirmConflict(self, new_label: str, conflict_with: str) -> bool:
        """A captured binding collides with an existing one — warn the
        user which binding it's already used for and let them choose
        whether to keep it anyway. Returns True to keep/commit the new
        binding, False to discard it. Menu Toggle / Quit conflicts never
        reach here — see _blockProtectedHotkey(), called first in
        _onFromCaptured / _onEditFromCaptured."""
        return QMessageBox.question(
            self, "Keybind already in use",
            f"{new_label} is already used for {conflict_with}.\n\n"
            f"Use it here anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

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

    def _startCapture(self, prompt: str, callback, allow_scroll: bool = False):
        self._capturing       = True
        self._pendingCallback = callback
        self._captureLabel.setText(prompt)
        self._captureLabel.setStyleSheet(
            f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 4px 10px;")
        self._captureLabel.setVisible(True)
        threading.Thread(target=self._captureThread,
                         args=(allow_scroll,), daemon=True).start()

    def _captureThread(self, allow_scroll: bool):
        result = None
        failed = False
        inter = None
        try:
            inter = bringUpInterception(
                lambda i: (
                    i.set_filter(i.is_keyboard,
                                 interception.FilterKeyFlag.FILTER_KEY_ALL),
                    i.set_filter(i.is_mouse,
                                 interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL),
                ),
                should_continue=lambda: self._capturing,
                context="remapper-capture",
            )
            if inter is None:
                failed = True
            else:
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
            destroyInterception(inter)

        self._pendingFailed = failed
        self._pendingResult = result  # None: callers already treat this as a no-op
        self._captureDone.emit()

    @Slot()
    def _onCaptureDone(self):
        self._captureLabel.setVisible(False)
        cb     = self._pendingCallback
        result = self._pendingResult
        failed = self._pendingFailed
        self._pendingCallback = None
        self._pendingResult   = None
        self._pendingFailed   = False
        if failed:
            self._captureLabel.setText("Capture failed — try again")
            self._captureLabel.setStyleSheet("color: #ff6666; font: italic 9pt 'Segoe UI';"
                                              " padding: 0px 10px 4px 10px;")
            self._captureLabel.setVisible(True)
            QTimer.singleShot(1800, lambda: self._captureLabel.setVisible(False))
            return
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
