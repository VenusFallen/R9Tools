import logging
import sys
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                                QPushButton)

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
    # Internal: hands install+quit off to the Qt main thread from a
    # background download thread, since QApplication.quit() must be
    # called from the main thread.
    _sigDoInstall = Signal()

    def __init__(self, parent, settings: dict, onSettingsChanged,
                 onCapture=None, onThemeChanged=None):
        super().__init__(parent, right_anchor=True)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._onCapture         = onCapture
        self._onThemeChanged    = onThemeChanged

        self._appState        = "idle"
        self._appLatestVer    = ""
        self._appInstallerPath = None

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

        # ---- Running Indicator ----
        # Simple "R9Tools is loaded" badge toggle — intentionally separate
        # from the module indicator (R / RF) settings on the Overlay tab.
        self._layout.addWidget(_sep())
        riLbl = QLabel("Running Indicator")
        riLbl.setStyleSheet(
            f"color: {theme.DIM}; font: bold 8pt 'Segoe UI'; padding: 2px 12px;")
        self._layout.addWidget(riLbl)
        riDescLbl = QLabel(
            "Shows a small \"R9\" badge in the top-right corner to confirm "
            "R9Tools is loaded.")
        riDescLbl.setStyleSheet(f"color: {theme.LABEL_FG}; padding: 0px 12px 4px 12px;")
        riDescLbl.setWordWrap(True)
        self._layout.addWidget(riDescLbl)

        ri = self._settings.setdefault("running_indicator", {"enabled": True})
        riRow = QFrame()
        rl = QHBoxLayout(riRow)
        rl.setContentsMargins(10, 3, 10, 3)
        rl.setSpacing(4)
        riEnLbl = QLabel("Enabled", riRow)
        riEnLbl.setFixedWidth(120)
        rl.addWidget(riEnLbl)
        self._riEnabledSwitch = theme.ToggleSwitch(
            riRow, value=ri.get("enabled", True), command=self._onRiToggle)
        rl.addWidget(self._riEnabledSwitch)
        rl.addStretch()
        self._layout.addWidget(riRow)

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
            onCapture=self._onCapture,
            settings=self._settings, exclude_id="hotkey:overlay_toggle")
        self._layout.addWidget(self._overlayBtn)

        self._quitBtn = theme.KeybindButton(
            self, "Quit:",
            binding=hk["quit"],
            onChange=self._onQuitChange,
            onCapture=self._onCapture,
            settings=self._settings, exclude_id="hotkey:quit")
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
            onCapture=self._onCapture,
            settings=self._settings, exclude_id="hotkey:recoil_toggle")
        self._layout.addWidget(self._recoilBtn)

        self._strengthUpBtn = theme.KeybindButton(
            self, "Strength +:",
            binding=hk["recoil_strength_up"],
            onChange=self._onStrengthUpChange,
            onCapture=self._onCapture,
            settings=self._settings, exclude_id="hotkey:recoil_strength_up")
        self._layout.addWidget(self._strengthUpBtn)

        self._strengthDownBtn = theme.KeybindButton(
            self, "Strength -:",
            binding=hk["recoil_strength_down"],
            onChange=self._onStrengthDownChange,
            onCapture=self._onCapture,
            settings=self._settings, exclude_id="hotkey:recoil_strength_down")
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

        # Manual fallback: always-visible link to the GitHub releases page.
        # This is independent of the Check/Update/Restart flow above — it
        # never calls into updater.py and always points at the general
        # releases page (never a specific tag).
        appLinkRow = QFrame(appCard)
        alr = QHBoxLayout(appLinkRow)
        alr.setContentsMargins(10, 0, 10, 6)
        alr.setSpacing(6)
        appLink = QLabel(
            f'<a href="https://github.com/VenusFallen/R9Tools/releases" '
            f'style="color:{theme.ACCENT}; text-decoration:none;">'
            f'View releases on GitHub</a>',
            appLinkRow)
        appLink.setOpenExternalLinks(True)
        appLink.setCursor(Qt.CursorShape.PointingHandCursor)
        appLink.setStyleSheet(f"color: {theme.DIM}; font: 8pt 'Segoe UI';")
        alr.addWidget(appLink, 1)
        appCard.layout().addWidget(appLinkRow)

        # Automatic check-on-launch toggle — a persistent app-behavior
        # preference (not forced off on profile load, like running_indicator
        # above), separate from this card's manual Check/Update/Install flow.
        aucRow = QFrame(appCard)
        aucl = QHBoxLayout(aucRow)
        aucl.setContentsMargins(10, 0, 10, 6)
        aucl.setSpacing(4)
        aucLbl = QLabel("Automatically check for updates", aucRow)
        aucLbl.setStyleSheet(f"color: {theme.LABEL_FG};")
        aucl.addWidget(aucLbl, 1)
        auc = self._settings.setdefault("auto_update_check", {"enabled": True})
        self._aucEnabledSwitch = theme.ToggleSwitch(
            aucRow, value=auc.get("enabled", True), command=self._onAucToggle)
        aucl.addWidget(self._aucEnabledSwitch)
        appCard.layout().addWidget(aucRow)

        # Wire up background-thread → UI signals
        self._sigAppStatus.connect(self._appStatusLbl.setText)
        self._sigAppBtn.connect(
            lambda txt, en: (self._appActionBtn.setText(txt),
                             self._appActionBtn.setEnabled(en)))
        self._sigDoInstall.connect(self._doInstallApp)

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

        # Running indicator — NOT forced off on profile switch (unlike the
        # other overlay toggles); just mirror whatever the profile has saved.
        ri = settings.setdefault("running_indicator", {"enabled": True})
        self._settings["running_indicator"] = ri
        self._riEnabledSwitch.set(ri.get("enabled", True))

        # Auto-update-check-on-launch — also NOT forced off on profile switch.
        auc = settings.setdefault("auto_update_check", {"enabled": True})
        self._settings["auto_update_check"] = auc
        self._aucEnabledSwitch.set(auc.get("enabled", True))

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

    def _onRiToggle(self):
        self._settings.setdefault("running_indicator", {})["enabled"] = \
            self._riEnabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onAucToggle(self):
        self._settings.setdefault("auto_update_check", {})["enabled"] = \
            self._aucEnabledSwitch.get()
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
            self._doInstallApp()

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
            logging.exception("R9Tools update check failed")
            self._appState = "error"
            self._sigAppStatus.emit("Check failed")
            self._sigAppBtn.emit("Retry", True)

    def _startDownloadApp(self, auto_continue: bool = False):
        if not getattr(sys, "frozen", False):
            self._appState = "idle"
            self._sigAppStatus.emit("Dev build — skipped")
            self._sigAppBtn.emit("Check", True)
            return
        self._appState = "downloading"
        self._sigAppStatus.emit("Downloading...")
        self._sigAppBtn.emit("...", False)
        threading.Thread(target=self._doDownloadApp, args=(auto_continue,), daemon=True).start()

    def _doDownloadApp(self, auto_continue: bool = False):
        try:
            def prog(pct):
                self._sigAppStatus.emit(f"Downloading... {pct}%")
            self._appInstallerPath = updater.download_app(prog)
            self._appState = "ready"
            if auto_continue:
                # Auto-continue chains straight to install+quit rather than
                # stopping at "Ready to install"; _doInstallApp() must run
                # on the Qt main thread, hence the signal hand-off.
                self._sigAppStatus.emit("Installing...")
                self._sigDoInstall.emit()
            else:
                self._sigAppStatus.emit("Ready to install")
                self._sigAppBtn.emit("Install", True)
        except Exception:
            logging.exception("R9Tools update download/extract failed")
            self._appState = "error"
            self._sigAppStatus.emit("Download failed")
            self._sigAppBtn.emit("Retry", True)

    def _doInstallApp(self):
        from PySide6.QtWidgets import QApplication
        try:
            updater.launch_installer_and_quit(self._appInstallerPath)
        except Exception:
            logging.exception("R9Tools update installer handoff failed")
            self._appState = "error"
            self._sigAppStatus.emit("Install failed")
            self._sigAppBtn.emit("Retry", True)
            return
        # The installer is now running detached and independent of this
        # process; quit so it can replace the currently-locked exe.
        QApplication.instance().quit()

    def triggerAutoUpdate(self, latest_version: str, on_status=None) -> None:
        """Start the download+install+quit flow for a version already
        confirmed available, reusing this panel's Update/Install code path
        but chaining straight through instead of stopping at "Ready to
        install". `on_status`, if given, mirrors this panel's status-text
        updates to an external caller (e.g. a startup progress dialog)."""
        self._appLatestVer = latest_version
        self._appState = "available"
        if on_status is not None:
            self._sigAppStatus.connect(on_status)
        self._startDownloadApp(auto_continue=True)


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
