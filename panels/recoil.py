import threading
import interception

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

import theme
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG, scancodeLabel

_POLL_MS = 100

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}


def _slotKeyLabel(sk: dict) -> str:
    t = sk.get("type", "key")
    if t == "mouse":
        return _MOUSE_DISPLAY.get(sk.get("button", ""), "?")
    if t == "scroll":
        return "Wheel Up" if sk.get("direction") == "up" else "Wheel Down"
    return scancodeLabel(sk.get("code", 0), sk.get("e0", False))


class _StatusDot(QWidget):
    """8 px dot with optional 14 px glow ring for the ACTIVE state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = "#888888"
        self._glow  = False
        self.setFixedSize(14, 14)

    def setState(self, color: str, glow: bool):
        self._color = color
        self._glow  = glow
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self._glow:
            c = QColor(self._color)
            c.setAlpha(70)
            p.setBrush(c)
            p.drawEllipse(0, 0, 14, 14)
        p.setBrush(QColor(self._color))
        p.drawEllipse(3, 3, 8, 8)
        p.end()


class RecoilPanel(Panel):

    _liveUpdate        = Signal(str)   # live display during recoil trigger capture
    _captureFinished   = Signal(list)  # final combo when recoil trigger released
    _rfTrigLiveUpdate  = Signal(str)   # live display during RF trigger capture
    _rfTrigCapFinished = Signal(list)  # final combo when RF trigger released
    _slotCaptureDone   = Signal()      # slot key capture done

    def __init__(self, parent, settings: dict, engine, onSettingsChanged):
        super().__init__(parent)
        self._settings          = settings
        self._engine            = engine
        self._onSettingsChanged = onSettingsChanged
        self._capturing         = False   # recoil trigger capture
        self._rfTrigCapturing   = False   # RF trigger capture
        self._slotCapturing     = False   # slot key capture
        self._slotCaptureCallback = None
        self._slotCaptureResult   = None

        self._liveUpdate.connect(self._onLiveUpdate)
        self._captureFinished.connect(self._finishCapture)
        self._rfTrigLiveUpdate.connect(self._onRfTrigLiveUpdate)
        self._rfTrigCapFinished.connect(self._finishRfTrigCapture)
        self._slotCaptureDone.connect(self._onSlotCaptureDone)

        self._build()

        self._pollTimer = QTimer(self)
        self._pollTimer.timeout.connect(self._pollStatus)
        self._pollTimer.start(_POLL_MS)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        self._buildRecoilSection()
        self._layout.addWidget(_sep())
        self._buildRfSection()

    def _buildRecoilSection(self):
        s = self._settings["recoil"]

        title = QLabel("Recoil Compensation")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        statusRow = QFrame()
        sl = QHBoxLayout(statusRow)
        sl.setContentsMargins(10, 0, 10, 4)
        sl.setSpacing(5)
        self._statusDot = _StatusDot(statusRow)
        sl.addWidget(self._statusDot)
        self._statusLabel = QLabel("OFF")
        self._statusLabel.setStyleSheet(
            f"color: {theme.DIM}; font: 8pt 'Segoe UI Variable Display', 'Segoe UI';")
        sl.addWidget(self._statusLabel)
        sl.addStretch()
        self._layout.addWidget(statusRow)

        self._layout.addWidget(_sep())

        card = theme.buildCard(self)
        self._syRow = theme.buildPlusMinusRow(
            card, "Pull Strength (px)", s["strength_y"], 1, 30, self._onSyChange)

        humanizeRow = QFrame(card)
        hl = QHBoxLayout(humanizeRow)
        hl.setContentsMargins(10, 3, 10, 3)
        hl.setSpacing(4)
        lbl = QLabel("Humanize", humanizeRow)
        lbl.setFixedWidth(120)
        hl.addWidget(lbl)
        self._humanizeSwitch = theme.ToggleSwitch(
            humanizeRow, value=s.get("humanize", False), command=self._onHumanizeChange)
        hl.addWidget(self._humanizeSwitch)
        hl.addStretch()
        card.layout().addWidget(humanizeRow)

        triggerRow = QFrame(card)
        tl = QHBoxLayout(triggerRow)
        tl.setContentsMargins(10, 3, 10, 6)
        tl.setSpacing(4)
        tlbl = QLabel("Trigger:", triggerRow)
        tlbl.setFixedWidth(120)
        tl.addWidget(tlbl)
        tl.addStretch()
        self._keybindBtn = QPushButton(theme.comboLabel(s["trigger_keys"]), triggerRow)
        self._keybindBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keybindBtn.clicked.connect(self._startCapture)
        tl.addWidget(self._keybindBtn)
        card.layout().addWidget(triggerRow)

    def _buildRfSection(self):
        rf = self._settings.get("rapidfire", {})

        # Title row with enabled toggle
        titleRow = QFrame()
        tl = QHBoxLayout(titleRow)
        tl.setContentsMargins(10, 6, 10, 2)
        tl.setSpacing(6)
        rfTitle = QLabel("Rapid Fire")
        rfTitle.setStyleSheet(f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';")
        tl.addWidget(rfTitle)
        tl.addStretch()
        self._rfEnabledSwitch = theme.ToggleSwitch(
            titleRow, value=rf.get("enabled", False), command=self._onRfEnabledChange)
        tl.addWidget(self._rfEnabledSwitch)
        self._layout.addWidget(titleRow)

        # RF status row
        rfStatusRow = QFrame()
        sl = QHBoxLayout(rfStatusRow)
        sl.setContentsMargins(10, 0, 10, 4)
        sl.setSpacing(5)
        self._rfStatusDot = _StatusDot(rfStatusRow)
        sl.addWidget(self._rfStatusDot)
        self._rfStatusLabel = QLabel("OFF")
        self._rfStatusLabel.setStyleSheet(
            f"color: {theme.DIM}; font: 8pt 'Segoe UI Variable Display', 'Segoe UI';")
        sl.addWidget(self._rfStatusLabel)
        sl.addStretch()
        self._layout.addWidget(rfStatusRow)

        self._layout.addWidget(_sep())

        # RF card: interval, humanize, fire trigger
        rfCard = theme.buildCard(self)
        self._rfIntervalRow = theme.buildPlusMinusRow(
            rfCard, "Interval (ms)", rf.get("interval_ms", 100), 10, 1000,
            self._onRfIntervalChange)

        rfHumRow = QFrame(rfCard)
        rhl = QHBoxLayout(rfHumRow)
        rhl.setContentsMargins(10, 3, 10, 3)
        rhl.setSpacing(4)
        rhlbl = QLabel("Humanize", rfHumRow)
        rhlbl.setFixedWidth(120)
        rhl.addWidget(rhlbl)
        self._rfHumanizeSwitch = theme.ToggleSwitch(
            rfHumRow, value=rf.get("humanize", False), command=self._onRfHumanizeChange)
        rhl.addWidget(self._rfHumanizeSwitch)
        rhl.addStretch()
        rfCard.layout().addWidget(rfHumRow)

        rfTrigRow = QFrame(rfCard)
        rtl = QHBoxLayout(rfTrigRow)
        rtl.setContentsMargins(10, 3, 10, 3)
        rtl.setSpacing(4)
        rtlbl = QLabel("Fire Trigger:", rfTrigRow)
        rtlbl.setFixedWidth(120)
        rtl.addWidget(rtlbl)
        rtl.addStretch()
        trig_keys = rf.get("trigger_keys", ["mouse_left"])
        self._rfTrigBtn = QPushButton(theme.comboLabel(trig_keys), rfTrigRow)
        self._rfTrigBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rfTrigBtn.clicked.connect(self._startRfTrigCapture)
        rtl.addWidget(self._rfTrigBtn)
        rfCard.layout().addWidget(rfTrigRow)

        # Weapon slots section
        slotLbl = QLabel("Weapon Slots")
        slotLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 6px 12px 2px 12px;")
        self._layout.addWidget(slotLbl)

        self._slotWidget = QWidget()
        self._slotWidget.setStyleSheet(f"background-color: {theme.PANEL_BG};")
        self._slotLayout = QVBoxLayout(self._slotWidget)
        self._slotLayout.setContentsMargins(10, 0, 10, 0)
        self._slotLayout.setSpacing(1)
        self._layout.addWidget(self._slotWidget)

        self._slotCaptureLabel = QLabel("")
        self._slotCaptureLabel.setStyleSheet(
            f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 2px 10px;")
        self._slotCaptureLabel.setVisible(False)
        self._layout.addWidget(self._slotCaptureLabel)

        addSlotBtn = QPushButton("+ Add Weapon Slot")
        addSlotBtn.setStyleSheet(
            f"text-align: left; padding: 3px 10px; margin: 4px 10px 8px 10px;")
        addSlotBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        addSlotBtn.clicked.connect(self._startAddSlot)
        self._layout.addWidget(addSlotBtn)

        self._refreshSlotRows()

    # ------------------------------------------------------------------
    # Slot rows
    # ------------------------------------------------------------------

    def _refreshSlotRows(self):
        while self._slotLayout.count():
            item = self._slotLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for sk in self._settings.get("rapidfire", {}).get("slot_keys", []):
            self._addSlotRow(sk)

    def _addSlotRow(self, sk: dict):
        row = QFrame(self._slotWidget)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(6)

        keyBtn = QPushButton(_slotKeyLabel(sk), row)
        keyBtn.setFixedWidth(80)
        keyBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        keyBtn.clicked.connect(lambda _, s=sk, b=keyBtn: self._editSlotKey(s, b))
        rl.addWidget(keyBtn)

        enableLbl = QLabel("RF:", row)
        enableLbl.setStyleSheet(f"color: {theme.DIM};")
        rl.addWidget(enableLbl)

        toggle = theme.ToggleSwitch(row, value=sk.get("enabled", True))
        def _cmd(s=sk, t=toggle):
            s["enabled"] = t.get()
            self._onSettingsChanged(self._settings)
        toggle._command = _cmd
        rl.addWidget(toggle)

        delBtn = QPushButton("×", row)
        delBtn.setFixedWidth(24)
        delBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        delBtn.setStyleSheet(
            f"QPushButton {{ color: #ff6666; background-color: {theme.BTN_BG}; border: none; }}"
            f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")
        delBtn.clicked.connect(lambda _, s=sk: self._deleteSlot(s))
        rl.addWidget(delBtn)

        rl.addStretch()
        self._slotLayout.addWidget(row)

    # ------------------------------------------------------------------
    # Slot actions
    # ------------------------------------------------------------------

    def _editSlotKey(self, sk: dict, btn: QPushButton):
        if self._slotCapturing or self._capturing or self._rfTrigCapturing:
            return

        def callback(result):
            if not result:
                return
            enabled = sk.get("enabled", True)
            sk.clear()
            sk.update(result)
            sk["enabled"] = enabled
            btn.setText(_slotKeyLabel(sk))
            self._onSettingsChanged(self._settings)

        self._startSlotCapture("Press slot key...", callback)

    def _deleteSlot(self, sk: dict):
        slot_keys = self._settings.get("rapidfire", {}).get("slot_keys", [])
        try:
            idx = next(i for i, s in enumerate(slot_keys) if s is sk)
            del slot_keys[idx]
        except StopIteration:
            pass
        self._refreshSlotRows()
        self._onSettingsChanged(self._settings)

    def _startAddSlot(self):
        if self._slotCapturing or self._capturing or self._rfTrigCapturing:
            return

        def callback(result):
            if not result:
                return
            new_sk = dict(result)
            new_sk["enabled"] = True
            self._settings.setdefault("rapidfire", {}).setdefault("slot_keys", []).append(new_sk)
            self._refreshSlotRows()
            self._onSettingsChanged(self._settings)

        self._startSlotCapture("Press weapon slot key...", callback)

    # ------------------------------------------------------------------
    # Slot key capture
    # ------------------------------------------------------------------

    def _startSlotCapture(self, prompt: str, callback):
        self._slotCapturing       = True
        self._slotCaptureCallback = callback
        self._slotCaptureResult   = None
        self._slotCaptureLabel.setText(prompt)
        self._slotCaptureLabel.setVisible(True)
        self._engine.setSuspendHotkeys(True)
        threading.Thread(target=self._slotCaptureThread, daemon=True).start()

    def _slotCaptureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)
        inter.set_filter(inter.is_mouse, interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        result = None
        try:
            while result is None:
                idx = inter.await_input(100)
                if idx is None or idx >= len(inter._devices):
                    continue
                device = inter._devices[idx]
                stroke = device.receive()
                if stroke is None:
                    continue
                device.send(stroke)

                if isinstance(stroke, interception.KeyStroke):
                    if stroke.flags & interception.KeyFlag.KEY_UP:
                        result = {
                            "code": stroke.code,
                            "e0":   bool(stroke.flags & interception.KeyFlag.KEY_E0),
                        }
                elif isinstance(stroke, interception.MouseStroke):
                    if stroke.button_flags & _SCROLL_WHEEL_FLAG:
                        delta = stroke.button_data
                        if delta > 32767:
                            delta -= 65536
                        result = {"type": "scroll", "direction": "up" if delta > 0 else "down"}
                    else:
                        for name, (down_flag, up_flag) in MOUSE_BUTTON_FLAGS.items():
                            if stroke.button_flags & up_flag:
                                result = {"type": "mouse", "button": name}
                                break
        finally:
            self._slotCapturing = False
            self._engine.setSuspendHotkeys(False)

        self._slotCaptureResult = result
        self._slotCaptureDone.emit()

    @Slot()
    def _onSlotCaptureDone(self):
        self._slotCaptureLabel.setVisible(False)
        cb     = self._slotCaptureCallback
        result = self._slotCaptureResult
        self._slotCaptureCallback = None
        self._slotCaptureResult   = None
        if cb:
            cb(result)

    # ------------------------------------------------------------------
    # Reload / engine callbacks
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        # Recoil
        s = settings["recoil"]
        self._settings["recoil"].update(s)
        self._syRow.set(s["strength_y"])
        self._keybindBtn.setText(theme.comboLabel(s["trigger_keys"]))
        self._humanizeSwitch.set(s.get("humanize", False))

        # Rapid fire
        rf = settings.get("rapidfire", {})
        self._settings.setdefault("rapidfire", {}).update(rf)
        self._rfEnabledSwitch.set(False)
        self._settings["rapidfire"]["enabled"] = False
        self._rfIntervalRow.set(rf.get("interval_ms", 100))
        self._rfHumanizeSwitch.set(rf.get("humanize", False))
        self._rfTrigBtn.setText(theme.comboLabel(rf.get("trigger_keys", ["mouse_left"])))
        self._refreshSlotRows()

    def updateStrength(self, value: int):
        self._syRow.set(value)

    # ------------------------------------------------------------------
    # Event handlers — Recoil
    # ------------------------------------------------------------------

    def _onSyChange(self):
        self._settings["recoil"]["strength_y"] = self._syRow.get()
        self._onSettingsChanged(self._settings)

    def _onHumanizeChange(self):
        self._settings["recoil"]["humanize"] = self._humanizeSwitch.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Event handlers — Rapid Fire
    # ------------------------------------------------------------------

    def _onRfEnabledChange(self):
        self._settings.setdefault("rapidfire", {})["enabled"] = self._rfEnabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onRfIntervalChange(self):
        self._settings.setdefault("rapidfire", {})["interval_ms"] = self._rfIntervalRow.get()
        self._onSettingsChanged(self._settings)

    def _onRfHumanizeChange(self):
        self._settings.setdefault("rapidfire", {})["humanize"] = self._rfHumanizeSwitch.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    def _pollStatus(self):
        # Recoil status
        if self._settings["recoil"]["enabled"] and self._engine.isActive:
            color, text, glow = "#22c55e", "ACTIVE", True
        elif self._settings["recoil"]["enabled"]:
            color, text, glow = "#ffaa00", "ON", False
        else:
            color, text, glow = theme.DIM, "OFF", False
        self._statusDot.setState(color, glow)
        self._statusLabel.setText(text)
        self._statusLabel.setStyleSheet(
            f"color: {color}; font: 8pt 'Segoe UI Variable Display', 'Segoe UI';")

        # RF status
        rf_enabled = self._settings.get("rapidfire", {}).get("enabled", False)
        armed  = self._engine.rfArmed
        firing = self._engine.rfFiring

        if rf_enabled and armed and firing:
            rc, rt, rg = "#22c55e", "FIRING", True
        elif rf_enabled and armed:
            rc, rt, rg = theme.ACCENT, "ARMED", False
        elif rf_enabled:
            rc, rt, rg = "#ffaa00", "ON", False
        else:
            rc, rt, rg = theme.DIM, "OFF", False
        self._rfStatusDot.setState(rc, rg)
        self._rfStatusLabel.setText(rt)
        self._rfStatusLabel.setStyleSheet(
            f"color: {rc}; font: 8pt 'Segoe UI Variable Display', 'Segoe UI';")

    # ------------------------------------------------------------------
    # Recoil trigger capture
    # ------------------------------------------------------------------

    def _startCapture(self):
        if self._capturing or self._rfTrigCapturing or self._slotCapturing:
            return
        self._capturing = True
        self._engine.setSuspendHotkeys(True)
        self._keybindBtn.setText("Hold keys...")
        self._keybindBtn.setStyleSheet(f"color: {theme.ACTIVE_FG};")
        threading.Thread(target=self._captureThread, daemon=True).start()

    def _captureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_mouse,
                         interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        inter.set_filter(inter.is_keyboard,
                         interception.FilterKeyFlag.FILTER_KEY_DOWN
                         | interception.FilterKeyFlag.FILTER_KEY_UP)
        held: set  = set()
        seen: list = []
        try:
            while self._capturing:
                idx = inter.await_input(100)
                if idx is None or idx >= len(inter._devices):
                    continue
                device = inter._devices[idx]
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
                                self._liveUpdate.emit(theme.comboLabel(seen) + " ...")
                        elif stroke.button_flags & upFlag:
                            held.discard(key)

                elif isinstance(stroke, interception.KeyStroke):
                    isE0 = bool(stroke.flags & interception.KeyFlag.KEY_E0)
                    name = scancodeLabel(stroke.code, isE0)
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(name)
                        if name not in seen:
                            seen.append(name)
                            self._liveUpdate.emit(theme.comboLabel(seen) + " ...")
                    else:
                        held.discard(name)

                if seen and not held:
                    break
        finally:
            self._capturing = False
            self._engine.setSuspendHotkeys(False)

        combo = seen if seen else self._settings["recoil"]["trigger_keys"]
        self._settings["recoil"]["trigger_keys"] = combo
        self._captureFinished.emit(combo)

    @Slot(str)
    def _onLiveUpdate(self, text: str):
        self._keybindBtn.setText(text)

    @Slot(list)
    def _finishCapture(self, combo: list):
        self._keybindBtn.setText(theme.comboLabel(combo))
        self._keybindBtn.setStyleSheet("")
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # RF trigger capture
    # ------------------------------------------------------------------

    def _startRfTrigCapture(self):
        if self._rfTrigCapturing or self._capturing or self._slotCapturing:
            return
        self._rfTrigCapturing = True
        self._engine.setSuspendHotkeys(True)
        self._rfTrigBtn.setText("Hold keys...")
        self._rfTrigBtn.setStyleSheet(f"color: {theme.ACTIVE_FG};")
        threading.Thread(target=self._rfTrigCaptureThread, daemon=True).start()

    def _rfTrigCaptureThread(self):
        inter = interception.Interception()
        inter.set_filter(inter.is_mouse,
                         interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)
        inter.set_filter(inter.is_keyboard,
                         interception.FilterKeyFlag.FILTER_KEY_DOWN
                         | interception.FilterKeyFlag.FILTER_KEY_UP)
        held: set  = set()
        seen: list = []
        try:
            while self._rfTrigCapturing:
                idx = inter.await_input(100)
                if idx is None or idx >= len(inter._devices):
                    continue
                device = inter._devices[idx]
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
                                self._rfTrigLiveUpdate.emit(theme.comboLabel(seen) + " ...")
                        elif stroke.button_flags & upFlag:
                            held.discard(key)

                elif isinstance(stroke, interception.KeyStroke):
                    isE0 = bool(stroke.flags & interception.KeyFlag.KEY_E0)
                    name = scancodeLabel(stroke.code, isE0)
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(name)
                        if name not in seen:
                            seen.append(name)
                            self._rfTrigLiveUpdate.emit(theme.comboLabel(seen) + " ...")
                    else:
                        held.discard(name)

                if seen and not held:
                    break
        finally:
            self._rfTrigCapturing = False
            self._engine.setSuspendHotkeys(False)

        combo = seen if seen else self._settings.get("rapidfire", {}).get("trigger_keys", ["mouse_left"])
        self._settings.setdefault("rapidfire", {})["trigger_keys"] = combo
        self._rfTrigCapFinished.emit(combo)

    @Slot(str)
    def _onRfTrigLiveUpdate(self, text: str):
        self._rfTrigBtn.setText(text)

    @Slot(list)
    def _finishRfTrigCapture(self, combo: list):
        self._rfTrigBtn.setText(theme.comboLabel(combo))
        self._rfTrigBtn.setStyleSheet("")
        self._onSettingsChanged(self._settings)


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
