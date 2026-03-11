"""
Frameless always-on-top control panel.
Implemented as two coordinated windows:
  _TopBarWindow  — full-screen-width topbar strip (separate window)
  PanelWindow    — PANEL_W-wide content area, hides when collapsed
"""
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

import profiles as prof
import theme
from theme import (PANEL_W, TOPBAR_H, TOPBAR_MARGIN_TOP, TOPBAR_MARGIN_SIDE,
                   TOPBAR_MARGIN_BOTTOM, TOPBAR_RADIUS, SHADOW_SIZE)

from panels.recoil    import RecoilPanel
from panels.crosshair import CrosshairPanel
from panels.remapper  import RemapperPanel
from panels.profiles  import ProfilesPanel
from panels.settings  import SettingsPanel

_TAB_RECOIL    = 0
_TAB_CROSSHAIR = 1
_TAB_REMAPPER  = 2
_TAB_PROFILES  = 3
_TAB_SETTINGS  = 4

_WIN_FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.Tool
)


# ---------------------------------------------------------------------------
# Topbar window
# ---------------------------------------------------------------------------

class _TopBarWindow(QWidget):
    """Full-screen-width topbar strip. Lives in its own window so it can span
    the full screen without affecting PanelWindow's PANEL_W width."""

    def __init__(self, panelWin: "PanelWindow"):
        super().__init__()
        self._panelWin  = panelWin
        self._activeTab = _TAB_RECOIL

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(_WIN_FLAGS)

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

    def _build(self):
        for label, index in [("WEAPON",    _TAB_RECOIL),
                              ("CROSSHAIR", _TAB_CROSSHAIR),
                              ("REMAPPER",  _TAB_REMAPPER)]:
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

    # ------------------------------------------------------------------
    # Public interface used by PanelWindow
    # ------------------------------------------------------------------

    def setActiveTab(self, index: int):
        self._activeTab = index
        for i, btn in self._tabButtons.items():
            self._styleTabButton(btn, i == index)

    def flashBorder(self, color: str):
        self._barFrame.setStyleSheet(
            f"QFrame#topBar {{ background-color: {theme.BAR_BG};"
            f" border-radius: {TOPBAR_RADIUS}px; border: 1.5px solid {color}; }}"
            f"QFrame#topBar * {{ background-color: transparent; }}")
        QTimer.singleShot(
            theme.FLASH_MS,
            lambda: self._applyBarStyle())

    def applyTheme(self):
        self._applyBarStyle()
        self._styleQuitButton()
        for i, btn in self._tabButtons.items():
            self._styleTabButton(btn, i == self._activeTab)

    def _applyBarStyle(self):
        self._barFrame.setStyleSheet(
            f"QFrame#topBar {{ background-color: {theme.BAR_BG};"
            f" border-radius: {TOPBAR_RADIUS}px; }}"
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
    """QFrame that re-clips itself to a rounded rect on every resize.
    This handles both normal layout changes and DPI-scaling geometry overrides."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if w > 0 and h > 0:
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, TOPBAR_RADIUS, TOPBAR_RADIUS)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))


# ---------------------------------------------------------------------------
# Panel content window
# ---------------------------------------------------------------------------

class PanelWindow(QWidget):

    def __init__(self, settings: dict, profileData: dict, engine, onSettingsChanged):
        super().__init__()
        self._settings         = settings
        self._profileData      = profileData
        self._engine           = engine
        self._settingsCallback = onSettingsChanged
        self._activeTab        = profileData.get("last_tab", _TAB_RECOIL)
        self._panelCollapsed   = False

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(_WIN_FLAGS)

        # Topbar lives in its own full-width window
        self._topBarWin = _TopBarWindow(self)

        self._buildLayout()
        self._buildPanels()
        self._applyThemeQSS()
        self._selectTab(self._activeTab)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _buildLayout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, SHADOW_SIZE, SHADOW_SIZE)
        root.setSpacing(0)

        self._contentFrame = _RoundedFrame()
        self._contentFrame.setObjectName("panelContent")
        self._contentFrame.setFixedWidth(PANEL_W)
        root.addWidget(self._contentFrame)

        shadow = QGraphicsDropShadowEffect(self._contentFrame)
        shadow.setBlurRadius(14)
        shadow.setOffset(2, 3)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._contentFrame.setGraphicsEffect(shadow)

        innerLayout = QVBoxLayout(self._contentFrame)
        innerLayout.setContentsMargins(0, 0, 0, 0)
        innerLayout.setSpacing(0)
        self._stack = QStackedWidget()
        innerLayout.addWidget(self._stack)

    # ------------------------------------------------------------------
    # Panels
    # ------------------------------------------------------------------

    def _buildPanels(self):
        def onChanged(s):
            self._settingsCallback(s)

        self._recoilPanel    = RecoilPanel(None, self._settings, self._engine, onChanged)
        self._crosshairPanel = CrosshairPanel(None, self._settings, self._engine, onChanged)
        self._remapperPanel  = RemapperPanel(None, self._settings, onChanged)
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
            _TAB_CROSSHAIR: self._crosshairPanel,
            _TAB_REMAPPER:  self._remapperPanel,
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
        self._topBarWin.setActiveTab(index)
        if not self._panelCollapsed:
            self._stack.setCurrentWidget(self._panels[index])
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
        if self._panels[self._activeTab].right_anchor:
            self.move(screen.width() - TOPBAR_MARGIN_SIDE - PANEL_W, y)
        else:
            self.move(TOPBAR_MARGIN_SIDE, y)
        self.adjustSize()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _onThemeChanged(self, name: str):
        theme.setTheme(name)
        self._applyTheme()

    def _applyThemeQSS(self):
        self.setStyleSheet(
            theme.makeQSS()
            + f"PanelWindow {{ background-color: transparent; }}"
            + f"QFrame#panelContent {{ background-color: {theme.PANEL_BG};"
            f" border-radius: {TOPBAR_RADIUS}px; }}")

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
        self._engine.updateSettings(settings)
        for key in settings:
            self._settings[key] = settings[key]

        if self._settings.get("theme", "Dark") != old_theme:
            theme.setTheme(self._settings["theme"])
            self._applyTheme()

        self._recoilPanel.reload(self._settings)
        self._crosshairPanel.reload(self._settings)
        self._remapperPanel.reload(self._settings)
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
                self._engine.updateSettings(settings)
                for key in settings:
                    self._settings[key] = settings[key]
                if self._settings.get("theme", "Dark") != old_theme:
                    theme.setTheme(self._settings["theme"])
                    self._applyTheme()
                self._recoilPanel.reload(self._settings)
                self._crosshairPanel.reload(self._settings)
                self._remapperPanel.reload(self._settings)
                self._settingsPanel.reload(self._settings)
