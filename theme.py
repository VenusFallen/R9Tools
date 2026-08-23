"""
Shared UI constants, widget helpers, and reusable widgets (Qt version).
Imported by all panel modules and by panel_window.py.
"""
import threading

from PySide6.QtCore import Qt, QTimer, QPoint, Signal, Slot
from PySide6.QtGui  import QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QLineEdit, QMessageBox,
)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
TOPBAR_H            = 36
TOPBAR_MARGIN_TOP   = 8
TOPBAR_MARGIN_SIDE  = 12
TOPBAR_MARGIN_BOTTOM = 4
TOPBAR_RADIUS       = 10
PANEL_W             = 260   # content width

FLASH_SAVE   = "#44ff88"
FLASH_LOAD   = "#4488ff"
FLASH_DELETE = "#ff4444"
FLASH_MS     = 400

# ---------------------------------------------------------------------------
# Theme palettes
# ---------------------------------------------------------------------------
THEMES = {
    "Dark": {
        "BAR_BG":       "#141414",
        "PANEL_BG":     "#1e1e1e",
        "BTN_BG":       "#2d2d2d",
        "BTN_FG":       "#ffffff",
        "LABEL_FG":     "#cccccc",
        "ACCENT":       "#4a9eff",
        "DIM":          "#888888",
        "ACTIVE_FG":    "#22c55e",
        "MINUS_FG":     "#f59e0b",
        "ENTRY_BG":     "#333333",
        "PANEL_BORDER": "#2a2a2a",
        "CARD_BG":      "#252525",
        "HOVER_BG":     "#3a3a3a",
        "TAB_HOVER_BG": "#202020",
    },
    "Light": {
        "BAR_BG":       "#c4c8cc",
        "PANEL_BG":     "#f0f2f4",
        "BTN_BG":       "#b0b6bc",
        "BTN_FG":       "#0d0d0d",
        "LABEL_FG":     "#2a2a2a",
        "ACCENT":       "#1a6fd4",
        "DIM":          "#606060",
        "ACTIVE_FG":    "#16a34a",
        "MINUS_FG":     "#d97706",
        "ENTRY_BG":     "#dde0e4",
        "PANEL_BORDER": "#9a9ea2",
        "CARD_BG":      "#e6e8ea",
        "HOVER_BG":     "#c8ccd0",
        "TAB_HOVER_BG": "#bbbfc3",
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
# (updated in-place by setTheme())
# ---------------------------------------------------------------------------
BAR_BG       = THEMES["Dark"]["BAR_BG"]
PANEL_BG     = THEMES["Dark"]["PANEL_BG"]
BTN_BG       = THEMES["Dark"]["BTN_BG"]
BTN_FG       = THEMES["Dark"]["BTN_FG"]
LABEL_FG     = THEMES["Dark"]["LABEL_FG"]
ACCENT       = THEMES["Dark"]["ACCENT"]
DIM          = THEMES["Dark"]["DIM"]
ACTIVE_FG    = THEMES["Dark"]["ACTIVE_FG"]
MINUS_FG     = THEMES["Dark"]["MINUS_FG"]
ENTRY_BG     = THEMES["Dark"]["ENTRY_BG"]
PANEL_BORDER = THEMES["Dark"]["PANEL_BORDER"]
CARD_BG      = THEMES["Dark"]["CARD_BG"]
HOVER_BG     = THEMES["Dark"]["HOVER_BG"]
TAB_HOVER_BG = THEMES["Dark"]["TAB_HOVER_BG"]

# ---------------------------------------------------------------------------
# Input constants
# ---------------------------------------------------------------------------
KEY_LABELS = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
}


def keyLabel(key: str) -> str:
    return KEY_LABELS.get(key, key.upper())


def comboLabel(keys: list) -> str:
    return " + ".join(keyLabel(k) for k in keys) if keys else "None"


# ---------------------------------------------------------------------------
# QSS stylesheet generator
# Call makeQSS() after setTheme() and pass result to widget.setStyleSheet().
# ---------------------------------------------------------------------------

def makeQSS() -> str:
    return (
        f"* {{ font-family: 'Segoe UI Variable Display', 'Segoe UI'; font-size: 9pt; }}"

        f"QWidget {{ background-color: {PANEL_BG}; color: {LABEL_FG}; }}"

        # Cards
        f"QFrame#card {{ background-color: {CARD_BG}; border-radius: 6px;"
        f" border: 1px solid {PANEL_BORDER}; }}"
        f"QFrame#card QLabel  {{ background-color: transparent; color: {LABEL_FG}; }}"
        f"QFrame#card QLineEdit {{ background-color: {ENTRY_BG}; color: {BTN_FG};"
        f"  border: none; padding: 2px 4px; border-radius: 3px; }}"
        f"QFrame#card QPushButton {{ background-color: {BTN_BG}; color: {BTN_FG};"
        f"  border: none; padding: 2px 4px; border-radius: 4px; }}"
        f"QFrame#card QPushButton:hover {{ background-color: {HOVER_BG}; }}"

        # General controls
        f"QPushButton {{ background-color: {BTN_BG}; color: {BTN_FG};"
        f"  border: none; padding: 3px 8px; border-radius: 4px; }}"
        f"QPushButton:hover {{ background-color: {HOVER_BG}; }}"
        f"QPushButton:pressed {{ background-color: {ENTRY_BG}; }}"

        f"QLabel {{ background-color: transparent; color: {LABEL_FG}; }}"

        f"QLineEdit {{ background-color: {ENTRY_BG}; color: {BTN_FG};"
        f"  border: none; padding: 2px 4px; border-radius: 3px; }}"

        f"QComboBox {{ background-color: {ENTRY_BG}; color: {BTN_FG};"
        f"  border: none; padding: 2px 6px; border-radius: 3px; }}"
        f"QComboBox QAbstractItemView {{ background-color: {ENTRY_BG}; color: {BTN_FG};"
        f"  selection-background-color: {BTN_BG}; selection-color: {BTN_FG}; }}"

        f"QScrollBar:vertical {{ background-color: {PANEL_BG}; width: 8px; border: none; }}"
        f"QScrollBar::handle:vertical {{ background-color: {BTN_BG}; border-radius: 4px; min-height: 20px; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"

        f"QPushButton#plusBtn  {{ color: {ACTIVE_FG}; }}"
        f"QPushButton#minusBtn {{ color: {MINUS_FG}; }}"
        f"QFrame#card QPushButton#plusBtn  {{ color: {ACTIVE_FG}; }}"
        f"QFrame#card QPushButton#minusBtn {{ color: {MINUS_FG}; }}"
    )


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def buildCard(parent: QWidget) -> QFrame:
    """Grouped control container with CARD_BG background. Adds itself to parent's layout."""
    card = QFrame(parent)
    card.setObjectName("card")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(0, 4, 0, 6)
    inner.setSpacing(0)
    if parent.layout():
        parent.layout().addWidget(card)
    return card


def sectionLabel(parent: QWidget, text: str) -> QLabel:
    """Uppercase dimmed section heading. Caller is responsible for adding to layout."""
    lbl = QLabel(text.upper(), parent)
    lbl.setStyleSheet(
        f"color: {DIM}; font: bold 7pt 'Segoe UI Variable Display', 'Segoe UI';"
        f" padding: 10px 10px 2px 10px;")
    return lbl


# ---------------------------------------------------------------------------
# PlusMinusRow
# ---------------------------------------------------------------------------

class _EditableEntry(QLineEdit):
    """Read-only QLineEdit that unlocks on click for direct editing."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)

    def mousePressEvent(self, event):
        if self.isReadOnly():
            self.setReadOnly(False)
            self.selectAll()
        super().mousePressEvent(event)


class PlusMinusRow(QWidget):
    """
    Label + [−] [value] [+] control row.
    .get() / .set() replace the old tk.IntVar pair.
    """

    def __init__(self, parent: QWidget, label: str, value: int,
                 minVal: int, maxVal: int, onChange, label_width: int = 120):
        super().__init__(parent)
        self._value    = value
        self._minVal   = minVal
        self._maxVal   = maxVal
        self._onChange = onChange
        self._inline   = label_width == 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0 if self._inline else 10, 3, 0 if self._inline else 10, 3)
        layout.setSpacing(4)

        if not self._inline:
            lbl = QLabel(label, self)
            lbl.setFixedWidth(label_width)
            layout.addWidget(lbl)

        self._minusBtn = QPushButton("−", self)
        self._minusBtn.setFixedSize(24, 24)
        self._minusBtn.setObjectName("minusBtn")
        self._minusBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._minusBtn.clicked.connect(lambda: self._adjust(-1))
        layout.addWidget(self._minusBtn)

        self._entry = _EditableEntry(str(value), self)
        self._entry.setFixedWidth(40)
        self._entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._entry.editingFinished.connect(self._onCommit)
        layout.addWidget(self._entry)

        self._plusBtn = QPushButton("+", self)
        self._plusBtn.setFixedSize(24, 24)
        self._plusBtn.setObjectName("plusBtn")
        self._plusBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plusBtn.clicked.connect(lambda: self._adjust(1))
        layout.addWidget(self._plusBtn)

        if not self._inline:
            layout.addStretch()

    def get(self) -> int:
        return self._value

    def set(self, v: int):
        self._value = max(self._minVal, min(self._maxVal, v))
        self._entry.setText(str(self._value))

    def _adjust(self, delta: int):
        self.set(self._value + delta)
        self._onChange()

    def _onCommit(self):
        if self._entry.isReadOnly():
            return
        try:
            val = max(self._minVal, min(self._maxVal, int(self._entry.text())))
            self._value = val
            self._entry.setText(str(val))
            self._onChange()
        except ValueError:
            self._entry.setText(str(self._value))
        self._entry.setReadOnly(True)


def buildPlusMinusRow(parent: QWidget, label: str, value: int,
                      minVal: int, maxVal: int, onChange) -> PlusMinusRow:
    """Create a PlusMinusRow and add it to parent's layout."""
    row = PlusMinusRow(parent, label, value, minVal, maxVal, onChange)
    if parent.layout():
        parent.layout().addWidget(row)
    return row


# ---------------------------------------------------------------------------
# ToggleSwitch
# ---------------------------------------------------------------------------

class ToggleSwitch(QWidget):
    """
    Pill-shaped animated toggle. Drop-in for the old tk-canvas version.
    .get() / .set() replace the old tk.BooleanVar pair.
    set() updates visuals without firing the command callback.
    """

    W     = 40
    H     = 20
    R     = 8
    PAD   = 2
    STEPS = 8
    DELAY = 15   # ms per animation step (~120 ms total)

    def __init__(self, parent=None, value: bool = False, command=None):
        super().__init__(parent)
        self._value          = value
        self._command        = command
        self._knobX          = float(self._targetX(value))
        self._target         = self._knobX
        self._step           = 0.0
        self._remain         = 0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._animateStep)

        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def get(self) -> bool:
        return self._value

    def set(self, v: bool):
        """Programmatic update — does NOT fire the command callback."""
        if v == self._value:
            return
        self._value = v
        self._startAnimation(self._targetX(v))

    # ------------------------------------------------------------------

    def _targetX(self, v: bool) -> float:
        return self.W - self.R - self.PAD if v else self.R + self.PAD

    def mousePressEvent(self, event):
        self._value = not self._value
        self._startAnimation(self._targetX(self._value))
        if self._command:
            self._command()

    def _startAnimation(self, target: float):
        if self._timer.isActive():
            self._timer.stop()
        self._target = target
        self._step   = (target - self._knobX) / self.STEPS
        self._remain = self.STEPS
        self._timer.start(self.DELAY)

    def _animateStep(self):
        if self._remain <= 0:
            self._knobX = self._target
            self.update()
            return
        self._knobX  += self._step
        self._remain -= 1
        self.update()
        self._timer.start(self.DELAY)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        p.setBrush(QColor(ACCENT if self._value else DIM))
        p.drawRoundedRect(0, 0, self.W, self.H, self.H // 2, self.H // 2)

        p.setBrush(QColor(PANEL_BG))
        p.drawEllipse(QPoint(round(self._knobX), self.H // 2), self.R, self.R)
        p.end()


# ---------------------------------------------------------------------------
# KeybindButton
# ---------------------------------------------------------------------------

class KeybindButton(QWidget):
    """
    Single-key capture widget for hotkey rebinding.
    Displays the current binding as a button label; click to capture a new key.
    Uses interception for capture so remapped keys are received correctly.

    Note: creates a second Interception() context while capturing.
    This is existing behaviour carried forward from the tkinter version.
    """

    # Internal signal: fired from capture thread → _finish runs on main thread
    # via automatic QueuedConnection (cross-thread signal delivery).
    _done = Signal()

    _MOUSE_LABELS = {
        "mouse_left":   "LMB",
        "mouse_right":  "RMB",
        "mouse_middle": "MMB",
        "mouse_x1":     "Mouse4",
        "mouse_x2":     "Mouse5",
    }

    def __init__(self, parent: QWidget, label: str, binding: dict,
                 onChange, onCapture=None, keyboard_only: bool = True,
                 settings: dict = None, exclude_id=None):
        super().__init__(parent)
        self._binding         = dict(binding)
        self._previousBinding = dict(binding)
        self._onChange        = onChange
        self._onCapture       = onCapture
        self._keyboard_only   = keyboard_only
        self._capturing       = False
        self._captureSuccess  = False
        # Set if the capture thread couldn't stand up the interception
        # driver context at all — distinct from a normal cancel/no-result,
        # so _finish() can show a real failure message instead of just
        # silently reverting to the previous binding.
        self._captureFailed   = False
        # Conflict-check wiring — settings is the full app settings dict and
        # exclude_id is this field's own registry id (see
        # keybind_conflicts.iterBindingSources), so re-capturing the same
        # key for the same field isn't reported as a conflict with itself.
        # When settings is None (no caller has opted in), conflict checking
        # is skipped entirely and behaviour is unchanged.
        self._settings   = settings
        self._exclude_id = exclude_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(4)

        lbl = QLabel(label, self)
        lbl.setFixedWidth(120)
        layout.addWidget(lbl)

        layout.addStretch()

        self._btn = QPushButton(self._bindingLabel(), self)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._startCapture)
        layout.addWidget(self._btn)

        self._done.connect(self._finish)

    def setBinding(self, binding: dict):
        self._binding         = dict(binding)
        self._previousBinding = dict(binding)
        self._btn.setText(self._bindingLabel())

    # ------------------------------------------------------------------

    def _bindingLabel(self) -> str:
        from recoil import scancodeLabel
        t = self._binding.get("type")
        if t == "mouse":
            button = self._binding.get("button", "")
            return self._MOUSE_LABELS.get(button, button or "(unbound)")
        if t == "scroll":
            direction = self._binding.get("direction", "up")
            return "Wheel Up" if direction == "up" else "Wheel Down"
        if not self._binding.get("code"):
            return "(unbound)"
        return scancodeLabel(self._binding["code"], self._binding["e0"])

    def _startCapture(self):
        if self._capturing:
            return
        self._capturing       = True
        self._captureSuccess  = False
        self._previousBinding = dict(self._binding)
        if self._onCapture:
            self._onCapture(True)
        prompt = "Press a key..." if self._keyboard_only else "Press key or button..."
        self._btn.setText(prompt)
        self._btn.setStyleSheet(f"color: {ACTIVE_FG};")
        threading.Thread(target=self._captureThread, daemon=True).start()

    def _captureThread(self):
        import interception as _ic
        from recoil import MOUSE_BUTTON_FLAGS, _SCROLL_WHEEL_FLAG
        from interception_bringup import bringUpInterception, destroyInterception

        inter = None
        try:
            def _configure(i):
                i.set_filter(i.is_keyboard, _ic.FilterKeyFlag.FILTER_KEY_ALL)
                if not self._keyboard_only:
                    i.set_filter(i.is_mouse, _ic.FilterMouseButtonFlag.FILTER_MOUSE_ALL)

            inter = bringUpInterception(
                _configure,
                should_continue=lambda: self._capturing,
                context="keybind-button-capture",
            )
            if inter is None:
                self._captureFailed = True
            else:
                while self._capturing:
                    idx = inter.await_input(100)
                    if idx is None:
                        continue
                    if idx >= len(inter._devices):
                        continue
                    device = inter._devices[idx]
                    stroke = device.receive()
                    if stroke is None:
                        continue
                    device.send(stroke)   # pass through to OS

                    if isinstance(stroke, _ic.KeyStroke):
                        if stroke.flags & _ic.KeyFlag.KEY_UP:
                            self._binding = {
                                "code": stroke.code,
                                "e0":   bool(stroke.flags & _ic.KeyFlag.KEY_E0),
                            }
                            self._captureSuccess = True
                            break
                    elif not self._keyboard_only and isinstance(stroke, _ic.MouseStroke):
                        # Scroll wheel
                        if stroke.button_flags & _SCROLL_WHEEL_FLAG:
                            delta = stroke.button_data
                            if delta > 32767:
                                delta -= 65536
                            self._binding = {
                                "type":      "scroll",
                                "direction": "up" if delta > 0 else "down",
                            }
                            self._captureSuccess = True
                            break
                        # Mouse buttons (on release)
                        for name, (down_flag, up_flag) in MOUSE_BUTTON_FLAGS.items():
                            if stroke.button_flags & up_flag:
                                self._binding = {"type": "mouse", "button": name}
                                self._captureSuccess = True
                                break
                        if self._captureSuccess:
                            break
        finally:
            self._capturing = False
            destroyInterception(inter)
            if self._onCapture:
                self._onCapture(False)
            self._done.emit()   # → _finish() on main thread

    @Slot()
    def _finish(self):
        if self._captureFailed:
            self._captureFailed = False
            self._btn.setText("Capture failed — try again")
            self._btn.setStyleSheet("color: #ff6666;")
            QTimer.singleShot(1800, self._revertFailedLabel)
            return
        if self._captureSuccess and self._settings is not None:
            import keybind_conflicts
            conflict = keybind_conflicts.findConflict(
                self._settings, self._binding, self._exclude_id)
            if conflict:
                newLabel = self._bindingLabel()
                # Menu Toggle and Quit (exclude_id "hotkey:overlay_toggle" /
                # "hotkey:quit") are a hard, non-overridable mutual
                # exclusion with remapper FROM sources and toggle bindings
                # specifically — if the new binding is already used as a
                # remap source or a toggle, revert immediately with no
                # dialog choice. Any other conflict for these two buttons
                # (or any conflict at all for the other three hotkey
                # buttons) still goes through the normal warn-and-confirm
                # flow below.
                if (self._exclude_id in ("hotkey:overlay_toggle", "hotkey:quit")
                        and keybind_conflicts.isProtectedSourceConflictLabel(conflict)):
                    usedBy = ("the remapper" if conflict.startswith("Remap:")
                              else "a toggle")
                    QMessageBox.warning(
                        self, "Keybind already in use",
                        f"{newLabel} cannot be used here: the new binding "
                        f"is already used by {usedBy}.",
                    )
                    self._binding        = self._previousBinding
                    self._captureSuccess = False
                else:
                    # No longer a hard-block for every other conflict type:
                    # warn the user which other binding already uses this
                    # key/button and let them decide whether to keep the
                    # new binding anyway or revert to what was bound before
                    # this capture. Either way we fall through to the
                    # normal commit/label-refresh logic below.
                    keep = QMessageBox.question(
                        self, "Keybind already in use",
                        f"{newLabel} is already used for {conflict}.\n\n"
                        f"Use it here anyway?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    ) == QMessageBox.StandardButton.Yes
                    if not keep:
                        self._binding        = self._previousBinding
                        self._captureSuccess = False
        self._btn.setText(self._bindingLabel())
        self._btn.setStyleSheet("")
        if self._captureSuccess:
            self._onChange(self._binding)

    def _revertFailedLabel(self):
        self._btn.setText(self._bindingLabel())
        self._btn.setStyleSheet("")
