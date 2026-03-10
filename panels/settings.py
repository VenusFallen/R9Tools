from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton

import theme
from panels.base import Panel

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


class SettingsPanel(Panel):

    def __init__(self, parent, settings: dict, onSettingsChanged,
                 onCapture=None, onThemeChanged=None):
        super().__init__(parent, right_anchor=True)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._onCapture         = onCapture
        self._onThemeChanged    = onThemeChanged
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        title = QLabel("Settings")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        # ---- Window Filter ----
        self._layout.addWidget(_sep())
        wfLbl = QLabel("Window Filter")
        wfLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px;")
        self._layout.addWidget(wfLbl)
        descLbl = QLabel(
            "Restrict active modules to the selected process.")
        descLbl.setStyleSheet(f"color: {theme.LABEL_FG}; padding: 0px 12px 4px 12px;")
        descLbl.setWordWrap(True)
        self._layout.addWidget(descLbl)

        filterCard = theme.buildCard(self)
        filterRow = QFrame(filterCard)
        fl = QHBoxLayout(filterRow)
        fl.setContentsMargins(10, 6, 10, 6)
        fl.setSpacing(4)
        self._windowCombo = QComboBox(filterRow)
        self._windowCombo.setMinimumWidth(150)
        self._windowCombo.setEditable(False)
        fl.addWidget(self._windowCombo)
        refreshBtn = QPushButton("↻", filterRow)
        refreshBtn.setFixedWidth(28)
        refreshBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        refreshBtn.clicked.connect(self._refreshProcesses)
        fl.addWidget(refreshBtn)
        fl.addStretch()
        filterCard.layout().addWidget(filterRow)

        self._refreshProcesses()
        self._windowCombo.currentTextChanged.connect(self._onWindowChange)

        # ---- Theme ----
        self._layout.addWidget(_sep())
        themeLbl = QLabel("Theme")
        themeLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px;")
        self._layout.addWidget(themeLbl)

        themeCard = theme.buildCard(self)
        themeRow = QFrame(themeCard)
        tl = QHBoxLayout(themeRow)
        tl.setContentsMargins(10, 4, 10, 6)
        tl.setSpacing(4)
        colorLbl = QLabel("Color Theme:", themeRow)
        colorLbl.setFixedWidth(100)
        tl.addWidget(colorLbl)
        self._themeBtns: dict[str, QPushButton] = {}
        current_theme = self._settings.get("theme", "Dark")
        for name in theme.THEME_NAMES:
            active = (name == current_theme)
            btn = QPushButton(name.upper(), themeRow)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, n=name: self._selectTheme(n))
            self._applyThemeButtonStyle(btn, active)
            tl.addWidget(btn)
            self._themeBtns[name] = btn
        tl.addStretch()
        themeCard.layout().addWidget(themeRow)

        # ---- Hotkeys header ----
        self._layout.addWidget(_sep())
        hkTitle = QLabel("Hotkeys")
        hkTitle.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 2px 10px 2px 10px;")
        self._layout.addWidget(hkTitle)

        # ---- General hotkeys ----
        self._layout.addWidget(_sep())
        genLbl = QLabel("General")
        genLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px 4px 12px;")
        self._layout.addWidget(genLbl)

        hk = self._settings["hotkeys"]
        self._overlayBtn = theme.KeybindButton(
            self, "Menu Toggle:",
            binding=hk["overlay_toggle"],
            onChange=self._onOverlayToggleChange,
            onCapture=self._onCapture)
        self._layout.addWidget(self._overlayBtn)

        self._quitBtn = theme.KeybindButton(
            self, "Quit:",
            binding=hk["quit"],
            onChange=self._onQuitChange,
            onCapture=self._onCapture)
        self._layout.addWidget(self._quitBtn)

        # ---- Recoil hotkeys ----
        self._layout.addWidget(_sep())
        recoilLbl = QLabel("Recoil")
        recoilLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px 4px 12px;")
        self._layout.addWidget(recoilLbl)

        self._recoilBtn = theme.KeybindButton(
            self, "Recoil Toggle:",
            binding=hk["recoil_toggle"],
            onChange=self._onRecoilToggleChange,
            onCapture=self._onCapture)
        self._layout.addWidget(self._recoilBtn)

        self._strengthUpBtn = theme.KeybindButton(
            self, "Strength +:",
            binding=hk["recoil_strength_up"],
            onChange=self._onStrengthUpChange,
            onCapture=self._onCapture)
        self._layout.addWidget(self._strengthUpBtn)

        self._strengthDownBtn = theme.KeybindButton(
            self, "Strength -:",
            binding=hk["recoil_strength_down"],
            onChange=self._onStrengthDownChange,
            onCapture=self._onCapture)
        self._layout.addWidget(self._strengthDownBtn)

    # ------------------------------------------------------------------
    # Process list
    # ------------------------------------------------------------------

    def _refreshProcesses(self):
        self._windowCombo.blockSignals(True)
        current = self._windowCombo.currentText()
        self._windowCombo.clear()
        self._windowCombo.addItem("")
        if _PSUTIL_AVAILABLE:
            try:
                names = sorted(set(
                    p.info["name"] for p in psutil.process_iter(["name"])
                    if p.info["name"]
                ))
                self._windowCombo.addItems(names)
            except Exception:
                pass
        if current:
            idx = self._windowCombo.findText(current)
            if idx >= 0:
                self._windowCombo.setCurrentIndex(idx)
        self._windowCombo.blockSignals(False)

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        # Window filter
        self._settings["window_filter"] = settings.get("window_filter", "")
        filter_val = self._settings["window_filter"]
        self._windowCombo.blockSignals(True)
        if filter_val and self._windowCombo.findText(filter_val) < 0:
            self._windowCombo.addItem(filter_val)
        self._windowCombo.setCurrentText(filter_val)
        self._windowCombo.blockSignals(False)

        # Theme
        new_theme = settings.get("theme", "Dark")
        self._settings["theme"] = new_theme
        for n, btn in self._themeBtns.items():
            self._applyThemeButtonStyle(btn, n == new_theme)

        # Hotkeys
        hk = settings.get("hotkeys", {})
        self._settings["hotkeys"].update(hk)
        self._overlayBtn.setBinding(hk["overlay_toggle"])
        self._quitBtn.setBinding(hk["quit"])
        self._recoilBtn.setBinding(hk["recoil_toggle"])
        self._strengthDownBtn.setBinding(hk["recoil_strength_down"])
        self._strengthUpBtn.setBinding(hk["recoil_strength_up"])

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _selectTheme(self, name: str):
        for n, btn in self._themeBtns.items():
            self._applyThemeButtonStyle(btn, n == name)
        self._settings["theme"] = name
        self._onSettingsChanged(self._settings)
        if self._onThemeChanged:
            self._onThemeChanged(name)

    def _applyThemeButtonStyle(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {theme.BTN_BG}; color: {theme.ACCENT};"
                f" border: none; padding: 2px 8px; }}")
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {theme.CARD_BG}; color: {theme.DIM};"
                f" border: none; padding: 2px 8px; }}"
                f"QPushButton:hover {{ background-color: {theme.HOVER_BG}; }}")

    def _onWindowChange(self, value: str):
        self._settings["window_filter"] = value
        self._onSettingsChanged(self._settings)

    def _onOverlayToggleChange(self, binding: dict):
        self._settings["hotkeys"]["overlay_toggle"] = binding
        self._onSettingsChanged(self._settings)

    def _onQuitChange(self, binding: dict):
        self._settings["hotkeys"]["quit"] = binding
        self._onSettingsChanged(self._settings)

    def _onRecoilToggleChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_toggle"] = binding
        self._onSettingsChanged(self._settings)

    def _onStrengthDownChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_strength_down"] = binding
        self._onSettingsChanged(self._settings)

    def _onStrengthUpChange(self, binding: dict):
        self._settings["hotkeys"]["recoil_strength_up"] = binding
        self._onSettingsChanged(self._settings)


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
