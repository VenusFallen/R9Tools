"""
Frameless always-on-top control panel.
Implemented as two coordinated windows:
  _TopBarWindow  — full-screen-width topbar strip (separate window)
  PanelWindow    — PANEL_W-wide content area, hides when collapsed
"""
import ctypes
import ctypes.wintypes as wintypes

from PySide6.QtCore import Qt, QAbstractNativeEventFilter, QTimer, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

import profiles as prof
import theme
from theme import (PANEL_W, TOPBAR_H, TOPBAR_MARGIN_TOP, TOPBAR_MARGIN_SIDE,
                   TOPBAR_MARGIN_BOTTOM, TOPBAR_RADIUS)

import device_watch
from panels.recoil    import RecoilPanel
from panels.overlay   import OverlayPanel
from panels.remapper  import RemapperPanel
from panels.profiles  import ProfilesPanel
from panels.settings  import SettingsPanel
from panels.macros    import MacrosPanel

_TAB_RECOIL    = 0
_TAB_OVERLAY   = 1
_TAB_REMAPPER  = 2
_TAB_MACROS    = 3
_TAB_PROFILES  = 4
_TAB_SETTINGS  = 5

_WIN_FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
)

_WM_DISPLAYCHANGE = 0x007E


# ---------------------------------------------------------------------------
# Display-resolution-change watcher
# ---------------------------------------------------------------------------

class _DisplayChangeFilter(QAbstractNativeEventFilter):
    """Watches raw Win32 messages for WM_DISPLAYCHANGE so the topbar/panel
    can react to a live resolution change (e.g. a game switching into a
    custom exclusive-fullscreen resolution) without requiring an app
    restart. QScreen's Qt-side signals aren't reliably emitted for a
    resolution change driven by another process's exclusive-fullscreen
    mode switch, so the raw message is hooked directly instead."""

    def __init__(self, onChange):
        super().__init__()
        self._onChange = onChange

    def nativeEventFilter(self, eventType, message):
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (ValueError, TypeError, OSError):
                return False, 0
            if msg.message == _WM_DISPLAYCHANGE:
                self._onChange()
        return False, 0


# ---------------------------------------------------------------------------
# Device-disable/reconnect watcher
# ---------------------------------------------------------------------------

class _DeviceChangeFilter(QAbstractNativeEventFilter):
    """Watches raw Win32 messages for WM_DEVICECHANGE and feeds parsed
    arrival/removal events into a device_watch.DeviceFailureWatcher.
    Mirrors _DisplayChangeFilter: hooks the existing Qt message pump
    instead of spinning up a separate message-only window/thread."""

    def __init__(self, watcher: "device_watch.DeviceFailureWatcher"):
        super().__init__()
        self._watcher = watcher

    def nativeEventFilter(self, eventType, message):
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (ValueError, TypeError, OSError):
                return False, 0
            if msg.message == device_watch.WM_DEVICECHANGE:
                parsed = device_watch.parse_device_change(msg.wParam, msg.lParam)
                if parsed is not None:
                    kind, instance_id = parsed
                    if kind == "removal":
                        self._watcher.handle_removal(instance_id)
                    else:
                        self._watcher.handle_arrival(instance_id)
        return False, 0


# ---------------------------------------------------------------------------
# Topbar window
# ---------------------------------------------------------------------------

class _TopBarWindow(QWidget):
    """Full-screen-width topbar strip. Lives in its own window so it can span
    the full screen without affecting PanelWindow's PANEL_W width."""

    def __init__(self, panelWin: "PanelWindow"):
        super().__init__()
        self._panelWin   = panelWin
        self._activeTab  = _TAB_RECOIL
        self._flashColor = ""   # non-empty while border flash is active

        self.setWindowFlags(_WIN_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        screenW = QApplication.primaryScreen().geometry().width()
        totalH  = TOPBAR_MARGIN_TOP + TOPBAR_H + TOPBAR_MARGIN_BOTTOM
        self.setFixedSize(screenW, totalH)
        self.move(0, 0)

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOPBAR_MARGIN_SIDE, TOPBAR_MARGIN_TOP,
            TOPBAR_MARGIN_SIDE, TOPBAR_MARGIN_BOTTOM)
        root.setSpacing(0)

        self._barFrame = QFrame()
        self._barFrame.setObjectName("topBar")
        self._barFrame.setFixedHeight(TOPBAR_H)
        self._barLayout = QHBoxLayout(self._barFrame)
        self._barLayout.setContentsMargins(4, 0, 4, 0)
        self._barLayout.setSpacing(0)
        root.addWidget(self._barFrame)

        self._tabButtons: dict[int, QPushButton] = {}
        self._build()
        self._applyBarStyle()

        # Keep a reference — QAbstractNativeEventFilter instances must stay
        # alive for as long as they're installed.
        self._displayFilter = _DisplayChangeFilter(self._onDisplayChange)
        QApplication.instance().installNativeEventFilter(self._displayFilter)

        # Device-disable/reconnect detection (see device_watch.py). Default
        # no-op callbacks — main.py supplies the real ones via
        # setDeviceFailureCallbacks(). winId() forces HWND creation, which
        # registerDeviceNotificationW requires.
        self._onDeviceFailedCb    = lambda ids: None
        self._onDeviceRecoveredCb = lambda ids: None
        self._deviceWatcher = device_watch.DeviceFailureWatcher(
            on_failed_batch=lambda ids: self._onDeviceFailedCb(ids),
            on_recovered=lambda ids: self._onDeviceRecoveredCb(ids),
        )
        self._deviceHandles = device_watch.register_device_notifications(int(self.winId()))
        self._deviceFilter = _DeviceChangeFilter(self._deviceWatcher)
        QApplication.instance().installNativeEventFilter(self._deviceFilter)

    def setDeviceFailureCallbacks(self, on_failed, on_recovered):
        """on_failed(ids: list[str]) / on_recovered(ids: list[str]) — see
        device_watch.DeviceFailureWatcher for exactly when/how each fires."""
        self._onDeviceFailedCb    = on_failed
        self._onDeviceRecoveredCb = on_recovered

    def _build(self):
        for label, index in [("CONTROLS", _TAB_RECOIL),
                              ("MACROS",   _TAB_MACROS),
                              ("REMAPPER", _TAB_REMAPPER),
                              ("OVERLAY",  _TAB_OVERLAY)]:
            self._barLayout.addWidget(self._makeTabButton(label, index))

        self._barLayout.addStretch()

        for label, index in [("PROFILES", _TAB_PROFILES),
                              ("SETTINGS", _TAB_SETTINGS)]:
            self._barLayout.addWidget(self._makeTabButton(label, index))

        self._quitBtn = QPushButton("✕")
        self._quitBtn.setFixedWidth(32)
        self._quitBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quitBtn.clicked.connect(QApplication.instance().quit)
        self._barLayout.addWidget(self._quitBtn)
        self._styleQuitButton()

    def _makeTabButton(self, label: str, index: int) -> QPushButton:
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _, i=index: self._panelWin._toggleTab(i))
        self._tabButtons[index] = btn
        return btn

    def paintEvent(self, event):
        """Draw the rounded bar background; WA_TranslucentBackground makes the rest invisible."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(
            TOPBAR_MARGIN_SIDE, TOPBAR_MARGIN_TOP,
            self.width() - 2 * TOPBAR_MARGIN_SIDE, TOPBAR_H,
            TOPBAR_RADIUS, TOPBAR_RADIUS,
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BAR_BG))
        p.drawPath(path)
        if self._flashColor:
            from PySide6.QtGui import QPen
            pen = QPen(QColor(self._flashColor), 1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        p.end()

    # ------------------------------------------------------------------
    # Resolution-change reactivity
    # ------------------------------------------------------------------

    def _onDisplayChange(self):
        """Re-query the (possibly changed) primary-monitor width and resize/
        reposition the full-width topbar to match; also nudges PanelWindow
        to reposition itself (right-anchored tabs' x-offset depends on
        screen width too) in case it happens to be visible at the moment
        the resolution changes."""
        screenW = QApplication.primaryScreen().geometry().width()
        totalH  = TOPBAR_MARGIN_TOP + TOPBAR_H + TOPBAR_MARGIN_BOTTOM
        self.setFixedSize(screenW, totalH)
        self.move(0, 0)
        self.update()
        self._panelWin._reposition()

    # ------------------------------------------------------------------
    # Public interface used by PanelWindow
    # ------------------------------------------------------------------

    def setActiveTab(self, index: int):
        self._activeTab = index
        for i, btn in self._tabButtons.items():
            self._styleTabButton(btn, i == index)

    def flashBorder(self, color: str):
        self._flashColor = color
        self.update()
        QTimer.singleShot(theme.FLASH_MS, self._clearFlash)

    def _clearFlash(self):
        self._flashColor = ""
        self.update()

    def applyTheme(self):
        self._applyBarStyle()
        self._styleQuitButton()
        for i, btn in self._tabButtons.items():
            self._styleTabButton(btn, i == self._activeTab)

    def _applyBarStyle(self):
        # Background is painted directly in paintEvent; frame itself is transparent.
        self._barFrame.setStyleSheet(
            f"QFrame#topBar {{ background-color: transparent; }}"
            f"QFrame#topBar * {{ background-color: transparent; }}")

    # ------------------------------------------------------------------
    # Button styling
    # ------------------------------------------------------------------

    def _styleTabButton(self, btn: QPushButton, active: bool):
        font = "bold 9pt 'Segoe UI Variable Display', 'Segoe UI'"
        if active:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {theme.BTN_FG};"
                f" border: none; border-bottom: 2px solid {theme.ACCENT};"
                f" padding: 3px 12px 1px 12px; font: {font}; }}")
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {theme.DIM};"
                f" border: none; border-bottom: 2px solid transparent;"
                f" padding: 3px 12px 1px 12px; font: {font}; }}"
                f"QPushButton:hover {{ color: {theme.LABEL_FG};"
                f" border-bottom-color: rgba(74,158,255,100); }}")

    def _styleQuitButton(self):
        font = "bold 9pt 'Segoe UI Variable Display', 'Segoe UI'"
        self._quitBtn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #ff6666;"
            f" border: none; border-bottom: 2px solid transparent;"
            f" padding: 3px 6px 1px 6px; font: {font}; }}"
            f"QPushButton:hover {{ color: #ff9999;"
            f" border-bottom-color: rgba(255,102,102,100); }}")


# ---------------------------------------------------------------------------
# Self-masking rounded content frame
# ---------------------------------------------------------------------------

class _RoundedFrame(QFrame):
    """QFrame with rounded corners via QSS border-radius (no mask needed with
    WA_TranslucentBackground on the parent window)."""
    pass


class _ContentStack(QStackedWidget):
    """QStackedWidget whose sizeHint reflects only the current page.

    Qt's QStackedLayout computes sizeHint()/minimumSizeHint() as the max over
    ALL pages ever added, regardless of which one is current or their size
    policy. That means a window sized via adjustSize() never shrinks back
    down after a larger tab has been shown once. Overriding these two hints
    to defer to currentWidget() fixes that at the source, for every tab."""

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


# ---------------------------------------------------------------------------
# Panel content window
# ---------------------------------------------------------------------------

class PanelWindow(QWidget):

    def __init__(self, settings: dict, profileData: dict, engine, macroEngine, onSettingsChanged):
        super().__init__()
        self._settings         = settings
        self._profileData      = profileData
        self._engine           = engine
        self._macroEngine      = macroEngine
        self._settingsCallback = onSettingsChanged
        _valid_tabs = {_TAB_RECOIL, _TAB_OVERLAY, _TAB_REMAPPER,
                       _TAB_MACROS, _TAB_PROFILES, _TAB_SETTINGS}
        _saved_tab             = profileData.get("last_tab", _TAB_RECOIL)
        self._activeTab        = _saved_tab if _saved_tab in _valid_tabs else _TAB_RECOIL
        self._panelCollapsed   = False

        self.setWindowFlags(_WIN_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        # Topbar lives in its own full-width window
        self._topBarWin = _TopBarWindow(self)

        self._buildLayout()
        self._buildPanels()
        self._applyThemeQSS()
        self._selectTab(self._activeTab)

    # ------------------------------------------------------------------
    # Layout / mask
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        """WA_TranslucentBackground makes untouched pixels invisible; draw nothing here —
        the content frame QSS provides the panel background."""
        pass

    def showEvent(self, event):
        """Re-run the size/position fix once the window is actually visible.

        A hidden top-level widget's sizeHint() can lag behind its true
        rendered size (Qt only fully activates layouts once shown), so
        _reposition() is redone here unconditionally rather than trusting
        callers to have sized things correctly before show()."""
        super().showEvent(event)
        self._reposition()

    def _buildLayout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._contentFrame = _RoundedFrame()
        self._contentFrame.setObjectName("panelContent")
        self._contentFrame.setFixedWidth(PANEL_W)  # initial default; _reposition()
        # sets the real per-tab width before the window is ever shown.
        root.addWidget(self._contentFrame)

        innerLayout = QVBoxLayout(self._contentFrame)
        innerLayout.setContentsMargins(0, 0, 0, 0)
        innerLayout.setSpacing(0)
        self._stack = _ContentStack()
        innerLayout.addWidget(self._stack)

    # ------------------------------------------------------------------
    # Panels
    # ------------------------------------------------------------------

    def _buildPanels(self):
        def onChanged(s):
            self._settingsCallback(s)

        self._recoilPanel    = RecoilPanel(None, self._settings, self._engine, onChanged)
        self._overlayPanel   = OverlayPanel(None, self._settings, self._engine, onChanged)
        self._remapperPanel  = RemapperPanel(None, self._settings, onChanged)
        self._macrosPanel    = MacrosPanel(None, self._settings, self._macroEngine, onChanged)
        self._profilesPanel  = ProfilesPanel(
            None, self._profileData,
            onLoad=self._onProfileLoad,
            onSave=self._onProfileSave,
            onDelete=self._onProfileDelete)
        self._settingsPanel  = SettingsPanel(
            None, self._settings, onChanged,
            onCapture=self._engine.setSuspendHotkeys,
            onThemeChanged=self._onThemeChanged)

        self._panels = {
            _TAB_RECOIL:    self._recoilPanel,
            _TAB_OVERLAY:   self._overlayPanel,
            _TAB_REMAPPER:  self._remapperPanel,
            _TAB_MACROS:    self._macrosPanel,
            _TAB_PROFILES:  self._profilesPanel,
            _TAB_SETTINGS:  self._settingsPanel,
        }
        for panel in self._panels.values():
            self._stack.addWidget(panel)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def _selectTab(self, index: int):
        self._activeTab = index
        self._profileData["last_tab"] = index
        self._topBarWin.setActiveTab(index)
        if not self._panelCollapsed:
            current = self._panels[index]
            for panel in self._panels.values():
                panel.setSizePolicy(
                    QSizePolicy.Policy.Preferred if panel is current else QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Preferred if panel is current else QSizePolicy.Policy.Ignored,
                )
            self._stack.setCurrentWidget(current)
        self._reposition()

    def _toggleTab(self, index: int):
        if index == self._activeTab:
            if self._panelCollapsed:
                self._expandPanel()
            else:
                self._collapsePanel()
        else:
            self._panelCollapsed = False
            self._selectTab(index)
            if not self.isVisible():
                self.show()
                self.raise_()
                self.activateWindow()

    def _shiftTab(self, direction: int):
        indices = sorted(self._panels.keys())
        current = indices.index(self._activeTab)
        self._selectTab(indices[(current + direction) % len(indices)])

    def _collapsePanel(self):
        if self._panelCollapsed:
            return
        self._panelCollapsed = True
        self.hide()

    def _expandPanel(self):
        if not self._panelCollapsed:
            return
        self._panelCollapsed = False
        self._selectTab(self._activeTab)
        self.show()
        self.raise_()
        self.activateWindow()

    def _reposition(self):
        screen = QApplication.primaryScreen().geometry()
        y = TOPBAR_MARGIN_TOP + TOPBAR_H + TOPBAR_MARGIN_BOTTOM
        activePanel = self._panels[self._activeTab]
        # Per-tab content width (defaults to PANEL_W; e.g. Macros opts into
        # a wider layout via Panel.panel_width). Applied here rather than
        # only in _selectTab() since showEvent()/_onDisplayChange() also
        # call _reposition() directly.
        panelW = getattr(activePanel, "panel_width", PANEL_W)
        self._contentFrame.setFixedWidth(panelW)
        if activePanel.right_anchor:
            self.move(screen.width() - TOPBAR_MARGIN_SIDE - panelW, y)
        else:
            self.move(TOPBAR_MARGIN_SIDE, y)
        # Force the cached layout hints to recompute for the newly-current
        # page before resizing, otherwise the window can keep the size the
        # largest previously-shown tab required. A plain invalidate() isn't
        # enough for a nested QStackedWidget (_stack, and MacrosPanel's own
        # inner stack) — each level's totalSizeHint() cache only refreshes
        # via activate(), called bottom-up (innermost stack first).
        self._stack.layout().invalidate()
        self._stack.layout().activate()
        self._contentFrame.layout().invalidate()
        self._contentFrame.layout().activate()
        self.layout().invalidate()
        self.layout().activate()
        self.resize(self.sizeHint())

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _onThemeChanged(self, name: str):
        theme.setTheme(name)
        self._applyTheme()

    def _applyThemeQSS(self):
        self.setStyleSheet(
            theme.makeQSS()
            + "PanelWindow { background-color: transparent; }"
            + f"QFrame#panelContent {{ background-color: {theme.PANEL_BG};"
            f" border-radius: {TOPBAR_RADIUS}px;"
            f" border: 1px solid {theme.PANEL_BORDER}; }}")

    def _applyTheme(self):
        self._applyThemeQSS()
        self._topBarWin.applyTheme()
        for w in self.findChildren(theme.ToggleSwitch):
            w.update()

    # ------------------------------------------------------------------
    # Border flash
    # ------------------------------------------------------------------

    def flashBorder(self, color: str):
        self._topBarWin.flashBorder(color)

    # ------------------------------------------------------------------
    # Device-disable/reconnect watcher (see device_watch.py) — the real
    # WM_DEVICECHANGE hook lives on _TopBarWindow (it owns the HWND);
    # this is just a passthrough so main.py only deals with PanelWindow.
    # ------------------------------------------------------------------

    def setDeviceFailureCallbacks(self, on_failed, on_recovered):
        self._topBarWin.setDeviceFailureCallbacks(on_failed, on_recovered)

    # ------------------------------------------------------------------
    # Show / hide (called from bridge via toggleOverlay slot)
    # ------------------------------------------------------------------

    @Slot()
    def toggleOverlay(self):
        if self._topBarWin.isVisible():
            self._profileData["last_tab"] = self._activeTab
            prof.save(self._profileData)
            self._panelCollapsed = False
            self.hide()
            self._topBarWin.hide()
        else:
            self._panelCollapsed = False
            self._stack.setCurrentWidget(self._panels[self._activeTab])
            self._selectTab(self._activeTab)
            self._topBarWin.show()
            self._topBarWin.raise_()
            self.show()
            self.raise_()
            self.activateWindow()

    # ------------------------------------------------------------------
    # Engine slots
    # ------------------------------------------------------------------

    @Slot(bool)
    def onRecoilToggled(self, state: bool):
        pass  # Engine already mutated shared cfg dict; poll timer picks up new state

    @Slot(int)
    def onStrengthChanged(self, value: int):
        self._recoilPanel.updateStrength(value)

    # ------------------------------------------------------------------
    # Auto-update (startup check) — forwards to the Settings panel, which
    # owns the actual updater.py-calling logic. See main.py's
    # _onUpdateAvailable for the caller.
    # ------------------------------------------------------------------

    def triggerAutoUpdate(self, latest_version: str, on_status=None) -> None:
        self._settingsPanel.triggerAutoUpdate(latest_version, on_status=on_status)

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._shiftTab(-1)
        elif key == Qt.Key.Key_Right:
            self._shiftTab(1)
        elif key == Qt.Key.Key_Up:
            self._collapsePanel()
        elif key == Qt.Key.Key_Down:
            self._expandPanel()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Profile callbacks
    # ------------------------------------------------------------------

    def _onProfileLoad(self, name: str):
        settings = prof.loadProfile(self._profileData, name)
        if settings is None:
            return
        old_theme = self._settings.get("theme", "Dark")
        self._engine.updateSettings(settings, full_reset=True)
        for key in settings:
            self._settings[key] = settings[key]

        if self._settings.get("theme", "Dark") != old_theme:
            theme.setTheme(self._settings["theme"])
            self._applyTheme()

        self._recoilPanel.reload(self._settings)
        self._overlayPanel.reload(self._settings)
        self._remapperPanel.reload(self._settings)
        self._macrosPanel.reload(self._settings)
        self._settingsPanel.reload(self._settings)
        self._profilesPanel.refreshCombo()
        self.flashBorder(theme.FLASH_LOAD)

    def _onProfileSave(self, name: str):
        if prof.saveProfile(self._profileData, name, self._settings):
            self._profilesPanel.refreshCombo()
            self.flashBorder(theme.FLASH_SAVE)

    def _onProfileDelete(self, name: str):
        was_active = (name == self._profileData.get("active"))
        if not prof.deleteProfile(self._profileData, name):
            return
        self._profilesPanel.refreshCombo()
        self.flashBorder(theme.FLASH_DELETE)
        if was_active:
            settings = prof.loadProfile(self._profileData, prof.DEFAULT_NAME)
            if settings:
                old_theme = self._settings.get("theme", "Dark")
                self._engine.updateSettings(settings, full_reset=True)
                for key in settings:
                    self._settings[key] = settings[key]
                if self._settings.get("theme", "Dark") != old_theme:
                    theme.setTheme(self._settings["theme"])
                    self._applyTheme()
                self._recoilPanel.reload(self._settings)
                self._overlayPanel.reload(self._settings)
                self._remapperPanel.reload(self._settings)
                self._macrosPanel.reload(self._settings)
                self._settingsPanel.reload(self._settings)
