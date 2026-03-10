import threading
import interception

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import theme
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, scancodeLabel

_POLL_MS = 100


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

    _liveUpdate      = Signal(str)   # live key display during capture
    _captureFinished = Signal(list)  # final combo when all keys released

    def __init__(self, parent, settings: dict, engine, onSettingsChanged):
        super().__init__(parent)
        self._settings          = settings
        self._engine            = engine
        self._onSettingsChanged = onSettingsChanged
        self._capturing         = False

        self._liveUpdate.connect(self._onLiveUpdate)
        self._captureFinished.connect(self._finishCapture)

        self._build()

        self._pollTimer = QTimer(self)
        self._pollTimer.timeout.connect(self._pollStatus)
        self._pollTimer.start(_POLL_MS)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        s = self._settings["recoil"]

        title = QLabel("Recoil Compensation")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        # Status row
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

        # Separator
        self._layout.addWidget(_sep())

        # Card: strength + humanize + trigger
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
        self._keybindBtn = QPushButton(
            theme.comboLabel(s["trigger_keys"]), triggerRow)
        self._keybindBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keybindBtn.clicked.connect(self._startCapture)
        tl.addWidget(self._keybindBtn)
        card.layout().addWidget(triggerRow)

    # ------------------------------------------------------------------
    # Reload / engine callback
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        s = settings["recoil"]
        self._settings["recoil"].update(s)
        self._syRow.set(s["strength_y"])
        self._keybindBtn.setText(theme.comboLabel(s["trigger_keys"]))
        self._humanizeSwitch.set(s.get("humanize", False))

    def updateStrength(self, value: int):
        self._syRow.set(value)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onSyChange(self):
        self._settings["recoil"]["strength_y"] = self._syRow.get()
        self._onSettingsChanged(self._settings)

    def _onHumanizeChange(self):
        self._settings["recoil"]["humanize"] = self._humanizeSwitch.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Status polling
    # ------------------------------------------------------------------

    def _pollStatus(self):
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

    # ------------------------------------------------------------------
    # Trigger capture
    # ------------------------------------------------------------------

    def _startCapture(self):
        if self._capturing:
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
                if idx is None:
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
                                self._liveUpdate.emit(
                                    theme.comboLabel(seen) + " ...")
                        elif stroke.button_flags & upFlag:
                            held.discard(key)

                elif isinstance(stroke, interception.KeyStroke):
                    isE0 = bool(stroke.flags & interception.KeyFlag.KEY_E0)
                    name = scancodeLabel(stroke.code, isE0)
                    if not (stroke.flags & interception.KeyFlag.KEY_UP):
                        held.add(name)
                        if name not in seen:
                            seen.append(name)
                            self._liveUpdate.emit(
                                theme.comboLabel(seen) + " ...")
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


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
