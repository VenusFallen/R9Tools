import sys
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                                QPushButton, QVBoxLayout)

import theme
import updater
from panels.base import Panel
from version import APP_VERSION

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


class SettingsPanel(Panel):

    # Signals emitted from background threads to update the UI safely
    _sigAppStatus = Signal(str)
    _sigAppBtn    = Signal(str, bool)   # (button_label, enabled)
    _sigLhmStatus = Signal(str)
    _sigLhmBtn    = Signal(str, bool)

    def __init__(self, parent, settings: dict, onSettingsChanged,
                 onCapture=None, onThemeChanged=None):
        super().__init__(parent, right_anchor=True)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._onCapture         = onCapture
        self._onThemeChanged    = onThemeChanged

        self._appState     = "idle"
        self._lhmState     = "idle"
        self._appLatestVer = ""

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

        # ---- Updates ----
        self._layout.addWidget(_sep())
        updTitle = QLabel("Updates")
        updTitle.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 2px 10px 2px 10px;")
        self._layout.addWidget(updTitle)

        # R9Tools update card
        self._layout.addWidget(_sep())
        appLbl = QLabel("R9Tools")
        appLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px 4px 12px;")
        self._layout.addWidget(appLbl)

        appCard = theme.buildCard(self)
        appRow  = QFrame(appCard)
        al = QHBoxLayout(appRow)
        al.setContentsMargins(10, 6, 10, 6)
        al.setSpacing(6)
        self._appStatusLbl = QLabel(f"v{APP_VERSION}", appRow)
        self._appStatusLbl.setStyleSheet(f"color: {theme.LABEL_FG};")
        al.addWidget(self._appStatusLbl, 1)
        self._appActionBtn = QPushButton("Check", appRow)
        self._appActionBtn.setFixedWidth(90)
        self._appActionBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._appActionBtn.clicked.connect(self._appBtnClicked)
        al.addWidget(self._appActionBtn)
        appCard.layout().addWidget(appRow)

        # LHM update card
        self._layout.addWidget(_sep())
        lhmLbl = QLabel("LHM DLLs")
        lhmLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px 4px 12px;")
        self._layout.addWidget(lhmLbl)

        lhmCard = theme.buildCard(self)
        lhmRow  = QFrame(lhmCard)
        ll = QHBoxLayout(lhmRow)
        ll.setContentsMargins(10, 6, 10, 6)
        ll.setSpacing(6)
        installed = updater.installed_lhm_version()
        lhm_ver_text = f"v{installed}" if installed else "Not installed"
        self._lhmStatusLbl = QLabel(lhm_ver_text, lhmRow)
        self._lhmStatusLbl.setStyleSheet(f"color: {theme.LABEL_FG};")
        ll.addWidget(self._lhmStatusLbl, 1)
        self._lhmActionBtn = QPushButton("Check", lhmRow)
        self._lhmActionBtn.setFixedWidth(90)
        self._lhmActionBtn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lhmActionBtn.clicked.connect(self._lhmBtnClicked)
        ll.addWidget(self._lhmActionBtn)
        lhmCard.layout().addWidget(lhmRow)

        # Wire up background-thread → UI signals
        self._sigAppStatus.connect(self._appStatusLbl.setText)
        self._sigAppBtn.connect(
            lambda txt, en: (self._appActionBtn.setText(txt),
                             self._appActionBtn.setEnabled(en)))
        self._sigLhmStatus.connect(self._lhmStatusLbl.setText)
        self._sigLhmBtn.connect(
            lambda txt, en: (self._lhmActionBtn.setText(txt),
                             self._lhmActionBtn.setEnabled(en)))

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
        merged = self._settings["hotkeys"]
        self._overlayBtn.setBinding(merged["overlay_toggle"])
        self._quitBtn.setBinding(merged["quit"])
        self._recoilBtn.setBinding(merged["recoil_toggle"])
        self._strengthDownBtn.setBinding(merged["recoil_strength_down"])
        self._strengthUpBtn.setBinding(merged["recoil_strength_up"])

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

    # ------------------------------------------------------------------
    # R9Tools update
    # ------------------------------------------------------------------

    def _appBtnClicked(self):
        if self._appState in ("idle", "up_to_date", "error"):
            self._startCheckApp()
        elif self._appState == "available":
            self._startDownloadApp()
        elif self._appState == "ready":
            self._doRestartApp()

    def _startCheckApp(self):
        self._appState = "checking"
        self._sigAppStatus.emit("Checking...")
        self._sigAppBtn.emit("...", False)
        threading.Thread(target=self._doCheckApp, daemon=True).start()

    def _doCheckApp(self):
        try:
            avail, latest = updater.check_app_update(APP_VERSION)
            if avail:
                self._appState     = "available"
                self._appLatestVer = latest
                self._sigAppStatus.emit(f"v{latest} available")
                self._sigAppBtn.emit("Update", True)
            else:
                self._appState = "up_to_date"
                self._sigAppStatus.emit("Up to date")
                self._sigAppBtn.emit("Check", True)
        except Exception:
            self._appState = "error"
            self._sigAppStatus.emit("Check failed")
            self._sigAppBtn.emit("Retry", True)

    def _startDownloadApp(self):
        if not getattr(sys, "frozen", False):
            self._appState = "idle"
            self._sigAppStatus.emit("Dev build — skipped")
            self._sigAppBtn.emit("Check", True)
            return
        self._appState = "downloading"
        self._sigAppStatus.emit("Downloading...")
        self._sigAppBtn.emit("...", False)
        threading.Thread(target=self._doDownloadApp, daemon=True).start()

    def _doDownloadApp(self):
        try:
            def prog(pct):
                self._sigAppStatus.emit(f"Downloading... {pct}%")
            updater.download_app(prog)
            self._appState = "ready"
            self._sigAppStatus.emit("Ready — restart to apply")
            self._sigAppBtn.emit("Restart Now", True)
        except Exception:
            self._appState = "error"
            self._sigAppStatus.emit("Download failed")
            self._sigAppBtn.emit("Retry", True)

    def _doRestartApp(self):
        from PySide6.QtWidgets import QApplication
        updater.restart_app()
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # LHM DLL update
    # ------------------------------------------------------------------

    def _lhmBtnClicked(self):
        if self._lhmState in ("idle", "up_to_date", "error"):
            self._startCheckLhm()
        elif self._lhmState == "available":
            self._startDownloadLhm()
        elif self._lhmState == "ready":
            self._doRestartApp()

    def _startCheckLhm(self):
        self._lhmState = "checking"
        self._sigLhmStatus.emit("Checking...")
        self._sigLhmBtn.emit("...", False)
        threading.Thread(target=self._doCheckLhm, daemon=True).start()

    def _doCheckLhm(self):
        try:
            avail, latest = updater.check_lhm_update()
            if avail:
                self._lhmState = "available"
                self._sigLhmStatus.emit(f"v{latest} available")
                self._sigLhmBtn.emit("Update", True)
            else:
                self._lhmState = "up_to_date"
                installed = updater.installed_lhm_version()
                self._sigLhmStatus.emit(f"v{installed} — up to date")
                self._sigLhmBtn.emit("Check", True)
        except Exception:
            self._lhmState = "error"
            self._sigLhmStatus.emit("Check failed")
            self._sigLhmBtn.emit("Retry", True)

    def _startDownloadLhm(self):
        self._lhmState = "downloading"
        self._sigLhmStatus.emit("Downloading...")
        self._sigLhmBtn.emit("...", False)
        threading.Thread(target=self._doDownloadLhm, daemon=True).start()

    def _doDownloadLhm(self):
        try:
            def prog(pct):
                self._sigLhmStatus.emit(f"Downloading... {pct}%")
            version = updater.download_lhm(prog)
            self._lhmState = "ready"
            self._sigLhmStatus.emit(f"v{version} — restart to apply")
            self._sigLhmBtn.emit("Restart Now", True)
        except Exception as e:
            self._lhmState = "error"
            print(f"[LHM update error] {e}")
            self._sigLhmStatus.emit(f"Failed: {e}")
            self._sigLhmBtn.emit("Retry", True)


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
