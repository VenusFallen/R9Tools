import threading

import interception

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget, QComboBox,
)

import theme
import keybind_conflicts
from panels.base import Panel
from recoil import MOUSE_BUTTON_FLAGS, scancodeLabel
from macro_engine import MacroEngine, actionLabel
from interception_bringup import bringUpInterception

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}

_MODE_OPTIONS = [("once", "ONCE"), ("hold", "HOLD"), ("toggle", "LOOP")]


class _ViewStack(QStackedWidget):
    """QStackedWidget whose sizeHint reflects only the current page.

    Same fix as panel_window._ContentStack, applied one level deeper: this
    stack toggles between the macro list and the editor. Left as a plain
    QStackedWidget, its sizeHint()/minimumSizeHint() get pinned to the max
    ever seen across both pages and never shrink back down (e.g. opening a
    macro with many actions, then a macro with few, keeps the window stuck
    at the taller size) — verified live via _reposition()/adjustSize()
    round-trips. Overriding these two hints to defer to currentWidget()
    fixes that at the source."""

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


def _triggerLabel(trig: dict) -> str:
    t = trig.get("type", "key")
    if t == "mouse":
        return _MOUSE_DISPLAY.get(trig.get("button", ""), "?")
    if t == "scroll":
        return "Whl Up" if trig.get("direction") == "up" else "Whl Dn"
    code = trig.get("code", 0)
    if not code:
        return "(none)"
    return scancodeLabel(code, trig.get("e0", False))


class MacrosPanel(Panel):

    # Caps for the list/editor scroll areas, in px. Raised from the old
    # 200/160 now that the Macros tab has its own wider/taller default
    # panel size (see Panel.panel_width above) so meaningfully more rows
    # are visible at once without inner scrolling.
    _MACRO_LIST_MAX_H  = 340
    _ACTION_LIST_MAX_H = 260

    _captureDone = Signal()

    def __init__(self, parent, settings: dict, macroEngine: MacroEngine, onSettingsChanged):
        # Wider/landscape default than the other tabs — the action-list and
        # macro-list scroll areas need more room (tester feedback: "macro
        # key-list menu is really small"). Still left-anchored like before.
        super().__init__(parent, panel_width=460)
        self._settings          = settings
        self._macroEngine       = macroEngine
        self._onSettingsChanged = onSettingsChanged
        self._editingMacro      = None
        self._editingIsNew      = False
        self._capturing         = False
        self._captureCallback   = None
        self._captureResult     = None
        self._captureFailed     = False
        self._recording         = False

        self._captureDone.connect(self._onCaptureDone)
        self._build()

    # ------------------------------------------------------------------
    # Top-level build
    # ------------------------------------------------------------------

    def _build(self):
        title = QLabel("Macros")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        self._viewStack = _ViewStack()
        self._layout.addWidget(self._viewStack)

        self._listView   = QWidget()
        self._editorView = QWidget()
        self._viewStack.addWidget(self._listView)    # index 0
        self._viewStack.addWidget(self._editorView)  # index 1

        self._buildListView()
        self._buildEditorView()
        self._showList()

    # ------------------------------------------------------------------
    # List view
    # ------------------------------------------------------------------

    def _buildListView(self):
        vl = QVBoxLayout(self._listView)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._macroListWidget = QWidget()
        self._macroListLayout = QVBoxLayout(self._macroListWidget)
        self._macroListLayout.setContentsMargins(10, 0, 10, 0)
        self._macroListLayout.setSpacing(2)

        self._macroScroll = QScrollArea()
        self._macroScroll.setWidget(self._macroListWidget)
        self._macroScroll.setWidgetResizable(True)
        self._macroScroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._macroScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._macroScroll.setFrameShape(QFrame.Shape.NoFrame)
        # Height driven by hand in _syncMacroScrollHeight() — see
        # _syncActionScrollHeight()'s docstring for why setMaximumHeight()
        # alone (relying on QScrollArea's own sizeHint()) isn't reliable
        # once macros are added/removed after the first layout pass.
        self._macroScroll.setFixedHeight(0)
        vl.addWidget(self._macroScroll)

        self._emptyLabel = QLabel("No macros yet.")
        self._emptyLabel.setStyleSheet(
            f"color: {theme.DIM}; font: italic 9pt 'Segoe UI'; padding: 6px 10px;")
        vl.addWidget(self._emptyLabel)

        addBtn = QPushButton("+ New Macro")
        addBtn.setStyleSheet(f"text-align: left; padding: 3px 10px; margin: 6px 10px 8px 10px;")
        addBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        addBtn.clicked.connect(self._newMacro)
        vl.addWidget(addBtn)

    def _refreshMacroRows(self):
        while self._macroListLayout.count():
            item = self._macroListLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        macros = self._settings.get("macros", [])
        self._emptyLabel.setVisible(len(macros) == 0)
        for macro in macros:
            self._addMacroRow(macro)
        QTimer.singleShot(0, self._resizeWindowToContent)

    def _addMacroRow(self, macro: dict):
        row = QFrame(self._macroListWidget)
        vl  = QVBoxLayout(row)
        vl.setContentsMargins(0, 3, 0, 3)
        vl.setSpacing(2)

        # ── Line 1: name (flexible) + toggle ──────────────────────────
        topRow = QWidget(row)
        tl = QHBoxLayout(topRow)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)

        full = macro.get("name", "Macro")
        name_lbl = QLabel(full, topRow)
        name_lbl.setStyleSheet(f"color: {theme.BTN_FG}; font-weight: bold;")
        name_lbl.setToolTip(full)
        tl.addWidget(name_lbl, stretch=1)

        tog = theme.ToggleSwitch(topRow, value=macro.get("enabled", True))
        tog._command = lambda m=macro, t=tog: self._syncToggleEnabled(m, t)
        tl.addWidget(tog)
        vl.addWidget(topRow)

        # ── Line 2: trigger badge + mode badge + Edit + × ─────────────
        botRow = QWidget(row)
        bl = QHBoxLayout(botRow)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)

        badge_style = (
            f"background-color: {theme.ENTRY_BG}; color: {theme.DIM};"
            f" padding: 1px 5px; border-radius: 3px; font-size: 8pt;")

        trig_lbl = QLabel(_triggerLabel(macro.get("trigger", {})), botRow)
        trig_lbl.setStyleSheet(badge_style)
        bl.addWidget(trig_lbl)

        mode_lbl = QLabel(macro.get("mode", "once").upper(), botRow)
        mode_lbl.setStyleSheet(badge_style)
        bl.addWidget(mode_lbl)

        bl.addStretch()

        editBtn = QPushButton("Edit", botRow)
        editBtn.setFixedWidth(44)
        editBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        editBtn.clicked.connect(lambda _, m=macro: self._editMacro(m))
        bl.addWidget(editBtn)

        delBtn = QPushButton("×", botRow)
        delBtn.setFixedWidth(24)
        delBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        delBtn.setStyleSheet(
            f"QPushButton {{ color: #ff6666; background-color: {theme.BTN_BG}; }}"
            f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")
        delBtn.clicked.connect(lambda _, m=macro: self._deleteMacro(m))
        bl.addWidget(delBtn)

        vl.addWidget(botRow)

        # thin separator line between rows
        sep = QFrame(self._macroListWidget)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.PANEL_BORDER};")

        self._macroListLayout.addWidget(row)
        self._macroListLayout.addWidget(sep)

    # ------------------------------------------------------------------
    # Editor view
    # ------------------------------------------------------------------

    def _buildEditorView(self):
        vl = QVBoxLayout(self._editorView)
        vl.setContentsMargins(0, 0, 0, 8)
        vl.setSpacing(0)

        # Back button
        backRow = QWidget()
        bl = QHBoxLayout(backRow)
        bl.setContentsMargins(10, 4, 10, 4)
        bl.setSpacing(4)
        # Label makes explicit that this both commits the macro's name (and,
        # for a brand-new macro, adds it to the list) *and* navigates back —
        # tester feedback was that a plain "← Back" label made the save
        # behavior easy to miss/discover only by accident.
        backBtn = QPushButton("✓ Save && Back")
        backBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        backBtn.clicked.connect(self._saveAndBack)
        bl.addWidget(backBtn)
        bl.addStretch()
        vl.addWidget(backRow)

        # Name
        nameRow = QWidget()
        nl = QHBoxLayout(nameRow)
        nl.setContentsMargins(10, 2, 10, 2)
        nl.setSpacing(6)
        nl.addWidget(QLabel("Name"))
        self._nameEdit = QLineEdit()
        self._nameEdit.setPlaceholderText("Macro name...")
        nl.addWidget(self._nameEdit)
        vl.addWidget(nameRow)

        # Trigger + Mode
        trigRow = QWidget()
        trl = QHBoxLayout(trigRow)
        trl.setContentsMargins(10, 2, 10, 2)
        trl.setSpacing(6)
        trl.addWidget(QLabel("Trigger"))
        self._trigBtn = QPushButton("(none)")
        self._trigBtn.setFixedWidth(68)
        self._trigBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trigBtn.clicked.connect(self._startTriggerCapture)
        trl.addWidget(self._trigBtn)
        trl.addStretch()
        trl.addWidget(QLabel("Mode"))
        self._modeCombo = QComboBox()
        for val, label in _MODE_OPTIONS:
            self._modeCombo.addItem(label, val)
        self._modeCombo.setFixedWidth(58)
        self._modeCombo.currentIndexChanged.connect(self._onModeChanged)
        trl.addWidget(self._modeCombo)
        vl.addWidget(trigRow)

        # Humanize
        humRow = QWidget()
        hl = QHBoxLayout(humRow)
        hl.setContentsMargins(10, 2, 10, 2)
        hl.setSpacing(6)
        self._humanizeSwitch = theme.ToggleSwitch(humRow, value=False,
                                                   command=self._onHumanizeChanged)
        hl.addWidget(self._humanizeSwitch)
        hl.addWidget(QLabel("Humanize delays"))
        hl.addStretch()
        vl.addWidget(humRow)

        # Record / Test
        recRow = QWidget()
        rl = QHBoxLayout(recRow)
        rl.setContentsMargins(10, 4, 10, 4)
        rl.setSpacing(6)
        self._recordBtn = QPushButton("● Record")
        self._recordBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recordBtn.clicked.connect(self._toggleRecord)
        rl.addWidget(self._recordBtn)
        self._testBtn = QPushButton("▶ Test")
        self._testBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._testBtn.clicked.connect(self._testMacro)
        rl.addWidget(self._testBtn)
        rl.addStretch()
        vl.addWidget(recRow)

        # Capture / record status label
        self._statusLabel = QLabel("")
        self._statusLabel.setStyleSheet(
            f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 2px 10px;")
        self._statusLabel.setVisible(False)
        vl.addWidget(self._statusLabel)

        # Actions header
        actHdr = QLabel("ACTIONS")
        actHdr.setStyleSheet(
            f"color: {theme.DIM}; font: bold 7pt 'Segoe UI Variable Display', 'Segoe UI';"
            f" padding: 6px 10px 2px 10px;")
        vl.addWidget(actHdr)

        # Scrollable action list
        self._actionListWidget = QWidget()
        self._actionListLayout = QVBoxLayout(self._actionListWidget)
        self._actionListLayout.setContentsMargins(10, 0, 10, 0)
        self._actionListLayout.setSpacing(2)

        self._actionScroll = QScrollArea()
        self._actionScroll.setWidget(self._actionListWidget)
        self._actionScroll.setWidgetResizable(True)
        self._actionScroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._actionScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._actionScroll.setFrameShape(QFrame.Shape.NoFrame)
        # QScrollArea.sizeHint() caches whatever the contained widget's size
        # was the first time the scroll area was laid out (observed: it
        # never re-measures itself when rows are added/removed afterward,
        # even after explicit layout invalidation) — so it's driven by hand
        # in _syncActionScrollHeight() instead of leaving it to size itself.
        self._actionScroll.setFixedHeight(0)
        vl.addWidget(self._actionScroll)

        # Add Action button
        self._addActionBtn = QPushButton("+ Add Action")
        self._addActionBtn.setStyleSheet(
            f"text-align: left; padding: 3px 10px; margin: 4px 10px 4px 10px;")
        self._addActionBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._addActionBtn.clicked.connect(self._showAddActionMenu)
        vl.addWidget(self._addActionBtn)

    def _populateEditor(self):
        m = self._editingMacro
        self._nameEdit.setText(m.get("name", ""))
        self._trigBtn.setText(_triggerLabel(m.get("trigger", {})))
        self._trigBtn.setStyleSheet("")

        mode = m.get("mode", "once")
        self._modeCombo.blockSignals(True)
        for i in range(self._modeCombo.count()):
            if self._modeCombo.itemData(i) == mode:
                self._modeCombo.setCurrentIndex(i)
                break
        self._modeCombo.blockSignals(False)

        self._humanizeSwitch.set(m.get("humanize", False))
        self._refreshActionRows()

    # ------------------------------------------------------------------
    # Action rows
    # ------------------------------------------------------------------

    def _refreshActionRows(self):
        while self._actionListLayout.count():
            item = self._actionListLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if self._editingMacro is not None:
            for action in self._editingMacro.get("actions", []):
                self._addActionRow(action)
        # Mirrors _refreshMacroRows()'s pattern: without this, the window's
        # height is only ever (re)computed the moment _showEditor() first
        # runs, so adding/deleting an action mid-session never grows/shrinks
        # the panel to fit — it just silently scrolls (or wastes space)
        # inside the capped-height action list instead.
        QTimer.singleShot(0, self._resizeWindowToContent)

    def _resizeWindowToContent(self):
        """Re-fit the panel window to whichever view (list or editor) is
        current. Shared by _refreshMacroRows(), _refreshActionRows() and
        _showList()/_showEditor() so every content mutation gets the same
        (verified-correct) resize treatment rather than each call site
        reinventing it slightly differently."""
        self._syncMacroScrollHeight()
        self._syncActionScrollHeight()
        # Bottom-up invalidate()+activate() of every nested layout between
        # here and the panel's own top level. invalidate() alone isn't
        # enough: _viewStack is a QStackedWidget, and an ancestor layout
        # asking for its contribution to a sizeHint computation bypasses
        # _ViewStack's Python-level sizeHint() override entirely, instead
        # reading _viewStack.layout()'s own separately-cached
        # totalSizeHint() (see PanelWindow._reposition()'s docstring for
        # the full explanation — this is the same bug one level deeper).
        # That cache is only refreshed by an explicit activate() call on
        # that exact layout, bottom-up, before the ancestor layouts (and
        # eventually PanelWindow._reposition()) query their own sizeHint.
        self._listView.layout().invalidate()
        self._editorView.layout().invalidate()
        self._viewStack.layout().invalidate()
        self._viewStack.layout().activate()
        self._layout.invalidate()
        self._layout.activate()
        win = self.window()
        reposition = getattr(win, "_reposition", None)
        if callable(reposition):
            # PanelWindow._reposition() resizes via resize(sizeHint()),
            # which — unlike adjustSize() — reliably shrinks a top-level
            # window back down, not just grows it (verified live: plain
            # adjustSize() gets stuck at the largest size ever reached).
            reposition()
        else:
            win.resize(win.sizeHint())

    def _syncActionScrollHeight(self):
        """Drive the action-list scroll area's height by hand.

        QScrollArea.sizeHint() only reflects the contained widget's size at
        the moment the scroll area was first laid out — it doesn't
        re-measure when rows are added/removed later (verified live: rows
        added after the initial show never grow sizeHint(), even after
        explicit layout invalidation). Since the outer window's auto-sizing
        depends on an accurate sizeHint from every widget in the chain,
        that stale value silently prevented the editor from ever resizing
        to fit its action list. Setting a fixed height from the content
        widget's own (reliable) sizeHint sidesteps the bug entirely.
        """
        content_h = self._actionListWidget.sizeHint().height()
        target_h = max(0, min(content_h, self._ACTION_LIST_MAX_H))
        self._actionScroll.setFixedHeight(target_h)

    def _syncMacroScrollHeight(self):
        """Drive the macro-list scroll area's height by hand.

        Same fix, same reason, as _syncActionScrollHeight() — this scroll
        area happened to look correct in earlier ad hoc checks only because
        those never actually removed a macro after the first layout pass;
        deleting macros afterward exposed the identical stale-sizeHint bug.
        """
        content_h = self._macroListWidget.sizeHint().height()
        target_h = max(0, min(content_h, self._MACRO_LIST_MAX_H))
        self._macroScroll.setFixedHeight(target_h)

    def _addActionRow(self, action: dict):
        type_str, val_str = actionLabel(action)
        row = QFrame(self._actionListWidget)
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(4)

        type_lbl = QLabel(type_str, row)
        type_lbl.setFixedWidth(72)
        rl.addWidget(type_lbl)

        if action.get("type") == "delay":
            val_field = QLineEdit(str(action.get("ms", 50)), row)
            val_field.setFixedWidth(46)
            val_field.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val_field.editingFinished.connect(
                lambda f=val_field, a=action: self._onDelayEdited(f, a))
            rl.addWidget(val_field)
            ms_lbl = QLabel("ms", row)
            ms_lbl.setStyleSheet(f"color: {theme.DIM};")
            rl.addWidget(ms_lbl)
        else:
            val_lbl = QLabel(val_str, row)
            val_lbl.setStyleSheet(
                f"background-color: {theme.ENTRY_BG}; color: {theme.BTN_FG};"
                f" padding: 2px 4px; border-radius: 3px;")
            val_lbl.setFixedWidth(68)
            rl.addWidget(val_lbl)

        rl.addStretch()

        delBtn = QPushButton("×", row)
        delBtn.setFixedWidth(20)
        delBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        delBtn.setStyleSheet(
            f"QPushButton {{ color: #ff6666; background-color: {theme.BTN_BG}; }}"
            f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")
        delBtn.clicked.connect(lambda _, a=action: self._deleteAction(a))
        rl.addWidget(delBtn)

        self._actionListLayout.addWidget(row)

    def _onDelayEdited(self, field: QLineEdit, action: dict):
        try:
            ms = max(1, min(60000, int(field.text())))
            action["ms"] = ms
            field.setText(str(ms))
            self._onSettingsChanged(self._settings)
        except ValueError:
            field.setText(str(action.get("ms", 50)))

    def _deleteAction(self, action: dict):
        actions = self._editingMacro.get("actions", [])
        try:
            idx = next(i for i, a in enumerate(actions) if a is action)
            del actions[idx]
        except StopIteration:
            pass
        self._refreshActionRows()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Add Action menu
    # ------------------------------------------------------------------

    def _showAddActionMenu(self):
        if self._capturing or self._recording:
            return
        menu = QMenu(self)
        menu.addAction("Key Tap",     lambda: self._addKeyAction("key_tap"))
        menu.addAction("Key Down",    lambda: self._addKeyAction("key_down"))
        menu.addAction("Key Up",      lambda: self._addKeyAction("key_up"))
        menu.addSeparator()
        menu.addAction("Mouse Click", lambda: self._addMouseAction("mouse_click"))
        menu.addAction("Mouse Down",  lambda: self._addMouseAction("mouse_down"))
        menu.addAction("Mouse Up",    lambda: self._addMouseAction("mouse_up"))
        menu.addSeparator()
        menu.addAction("Delay",       self._addDelayAction)
        menu.exec(self._addActionBtn.mapToGlobal(
            self._addActionBtn.rect().topLeft()))

    def _addKeyAction(self, action_type: str):
        self._startCapture(
            "Press a key...",
            lambda inp: self._onKeyActionCaptured(action_type, inp),
            keyboard_only=True)

    def _addMouseAction(self, action_type: str):
        self._startCapture(
            "Click a mouse button...",
            lambda inp: self._onMouseActionCaptured(action_type, inp),
            keyboard_only=False,
            mouse_only=True)

    def _addDelayAction(self):
        if self._editingMacro is None:
            return
        self._editingMacro.setdefault("actions", []).append({"type": "delay", "ms": 100})
        self._refreshActionRows()
        self._onSettingsChanged(self._settings)

    def _onKeyActionCaptured(self, action_type: str, inp: dict):
        if inp is None or self._editingMacro is None:
            return
        self._editingMacro.setdefault("actions", []).append({
            "type": action_type,
            "code": inp["code"],
            "e0":   inp.get("e0", False),
        })
        self._refreshActionRows()
        self._onSettingsChanged(self._settings)

    def _onMouseActionCaptured(self, action_type: str, inp: dict):
        if inp is None or self._editingMacro is None:
            return
        self._editingMacro.setdefault("actions", []).append({
            "type":   action_type,
            "button": inp.get("button", "mouse_left"),
        })
        self._refreshActionRows()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Trigger capture
    # ------------------------------------------------------------------

    def _startTriggerCapture(self):
        if self._capturing or self._recording:
            return
        self._trigBtn.setText("Press key...")
        self._trigBtn.setStyleSheet(f"color: {theme.ACTIVE_FG};")
        self._startCapture("", self._onTriggerCaptured, keyboard_only=False)

    def _onTriggerCaptured(self, inp: dict):
        if inp is None or self._editingMacro is None:
            self._trigBtn.setStyleSheet("")
            return
        exclude_id = f"macro_trigger:{id(self._editingMacro)}"
        conflict = keybind_conflicts.findConflict(self._settings, inp, exclude_id=exclude_id)
        if conflict:
            # Hard-block: discard the capture, explain why via the status
            # label (the same label used for capture/record prompts), and
            # immediately re-arm capture — the trigger button stays in
            # "Press key..." capture mode instead of reverting to the
            # macro's current trigger, so the user can just press another
            # key without clicking the button again.
            self._trigBtn.setText("Press key...")
            self._trigBtn.setStyleSheet(f"color: {theme.ACTIVE_FG};")
            self._statusLabel.setText(f"Already bound to {conflict}. Try again.")
            self._statusLabel.setStyleSheet(
                f"color: #ff6666; font: italic 9pt 'Segoe UI';"
                f" padding: 0px 10px 2px 10px;")
            self._statusLabel.setVisible(True)
            self._startCapture("", self._onTriggerCaptured, keyboard_only=False)
            QTimer.singleShot(2200, self._resetStatusLabel)
            return
        self._editingMacro["trigger"] = inp
        self._trigBtn.setText(_triggerLabel(inp))
        self._trigBtn.setStyleSheet("")
        self._onSettingsChanged(self._settings)

    def _resetStatusLabel(self):
        self._statusLabel.setVisible(False)
        self._statusLabel.setStyleSheet(
            f"color: {theme.ACTIVE_FG}; font: italic 9pt 'Segoe UI';"
            f" padding: 0px 10px 2px 10px;")

    # ------------------------------------------------------------------
    # Generic input capture (interception thread)
    # ------------------------------------------------------------------

    def _startCapture(self, prompt: str, callback, keyboard_only=True, mouse_only=False):
        if self._capturing:
            return
        self._capturing       = True
        self._captureCallback = callback
        if prompt:
            self._statusLabel.setText(prompt)
            self._statusLabel.setVisible(True)
        threading.Thread(
            target=self._captureThread,
            args=(keyboard_only, mouse_only),
            daemon=True).start()

    def _captureThread(self, keyboard_only: bool, mouse_only: bool):
        result = None
        failed = False
        try:
            def _configure(i):
                i.set_filter(i.is_keyboard, interception.FilterKeyFlag.FILTER_KEY_ALL)
                if not keyboard_only:
                    i.set_filter(i.is_mouse, interception.FilterMouseButtonFlag.FILTER_MOUSE_ALL)

            inter = bringUpInterception(
                _configure,
                should_continue=lambda: self._capturing,
                context="macro-capture",
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
                    if not mouse_only and isinstance(stroke, interception.KeyStroke):
                        if stroke.flags & interception.KeyFlag.KEY_UP:
                            result = {
                                "type": "key",
                                "code": stroke.code,
                                "e0":   bool(stroke.flags & interception.KeyFlag.KEY_E0),
                            }
                    elif not keyboard_only and isinstance(stroke, interception.MouseStroke):
                        for name, (dFlag, uFlag) in MOUSE_BUTTON_FLAGS.items():
                            if stroke.button_flags & uFlag:
                                result = {"type": "mouse", "button": name}
                                break
        finally:
            self._capturing = False

        self._captureFailed = failed
        self._captureResult = result  # None: user's callbacks already treat this as a no-op
        self._captureDone.emit()

    @Slot()
    def _onCaptureDone(self):
        self._statusLabel.setVisible(False)
        cb     = self._captureCallback
        result = self._captureResult
        failed = self._captureFailed
        self._captureCallback = None
        self._captureResult   = None
        self._captureFailed   = False
        if failed:
            self._statusLabel.setText("Capture failed — try again")
            self._statusLabel.setStyleSheet(
                f"color: #ff6666; font: italic 9pt 'Segoe UI';"
                f" padding: 0px 10px 2px 10px;")
            self._statusLabel.setVisible(True)
            QTimer.singleShot(1800, self._resetStatusLabel)
            return
        if cb:
            cb(result)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _toggleRecord(self):
        if not self._recording:
            self._recording = True
            self._recordBtn.setText("■ Stop")
            self._recordBtn.setStyleSheet(f"color: #ff6666;")
            self._statusLabel.setText("Recording... click Stop when done.")
            self._statusLabel.setVisible(True)
            self._macroEngine.startRecording()
        else:
            self._recording = False
            self._recordBtn.setText("● Record")
            self._recordBtn.setStyleSheet("")
            self._statusLabel.setVisible(False)
            actions = self._macroEngine.stopRecording()
            if self._editingMacro is not None:
                self._editingMacro["actions"] = actions
                self._refreshActionRows()
                self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Test
    # ------------------------------------------------------------------

    def _testMacro(self):
        if self._editingMacro is not None:
            self._macroEngine.testMacro(self._editingMacro)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _syncToggleEnabled(self, macro: dict, tog: theme.ToggleSwitch):
        macro["enabled"] = tog.get()
        self._onSettingsChanged(self._settings)

    def _onModeChanged(self):
        if self._editingMacro is None:
            return
        self._editingMacro["mode"] = self._modeCombo.currentData()
        self._onSettingsChanged(self._settings)

    def _onHumanizeChanged(self):
        if self._editingMacro is None:
            return
        self._editingMacro["humanize"] = self._humanizeSwitch.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _showList(self):
        if self._recording:
            self._macroEngine.stopRecording()
            self._recording = False
            self._recordBtn.setText("● Record")
            self._recordBtn.setStyleSheet("")
        self._listView.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._editorView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._viewStack.setCurrentIndex(0)
        # _refreshMacroRows() already schedules the resize (via
        # _resizeWindowToContent) once the rows are rebuilt, so no separate
        # adjustSize() call is needed here.
        self._refreshMacroRows()

    def _showEditor(self):
        self._listView.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._editorView.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._viewStack.setCurrentIndex(1)
        # _populateEditor() -> _refreshActionRows() already schedules the
        # resize (via _resizeWindowToContent) once the action rows are
        # actually populated, so no separate adjustSize() call is needed
        # here.
        self._populateEditor()

    def _newMacro(self):
        self._editingMacro = {
            "name":     "New Macro",
            "enabled":  True,
            "trigger":  {},
            "mode":     "once",
            "humanize": False,
            "actions":  [],
        }
        self._editingIsNew = True
        self._showEditor()

    def _editMacro(self, macro: dict):
        self._editingMacro = macro
        self._editingIsNew = False
        self._showEditor()

    def _saveAndBack(self):
        if self._recording:
            self._toggleRecord()
            return
        if self._editingMacro is not None:
            name = self._nameEdit.text().strip()
            self._editingMacro["name"] = name or "New Macro"
            if self._editingIsNew:
                self._settings.setdefault("macros", []).append(self._editingMacro)
                self._editingIsNew = False
            self._onSettingsChanged(self._settings)
            # Brief visual confirmation that this button just saved (in
            # addition to navigating back) — same green flashBorder()
            # affordance already used for profile saves elsewhere, chosen
            # over the in-panel _statusLabel flash used for conflict
            # rejection since that label lives on the editor view and would
            # disappear the instant _showList() below switches views.
            flashBorder = getattr(self.window(), "flashBorder", None)
            if callable(flashBorder):
                flashBorder(theme.FLASH_SAVE)
        self._editingMacro = None
        self._showList()

    def _deleteMacro(self, macro: dict):
        macros = self._settings.get("macros", [])
        try:
            idx = next(i for i, m in enumerate(macros) if m is macro)
            del macros[idx]
        except StopIteration:
            pass
        self._refreshMacroRows()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Reload (profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        self._settings = settings
        if self._recording:
            self._macroEngine.stopRecording()
            self._recording = False
        self._editingMacro = None
        self._showList()
