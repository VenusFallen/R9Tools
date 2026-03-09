import tkinter as tk
from tkinter import ttk
import profiles as prof

import theme
from panels.recoil    import RecoilPanel
from panels.crosshair import CrosshairPanel
from panels.remapper  import RemapperPanel
from panels.profiles  import ProfilesPanel
from panels.settings  import SettingsPanel

# Tab index constants
_TAB_RECOIL    = 0
_TAB_CROSSHAIR = 1
_TAB_REMAPPER  = 2
_TAB_PROFILES  = 3
_TAB_SETTINGS  = 4


class Overlay:
    def __init__(self, settings: dict, profileData: dict, engine, onSettingsChanged):
        self._settings          = settings
        self._profileData       = profileData
        self._engine            = engine
        self._onSettingsChanged = onSettingsChanged
        self._visible           = False
        self._activeTab         = _TAB_RECOIL
        self._buildWindow()

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _buildWindow(self):
        self._root = tk.Tk()
        self._root.title("R9Tools")
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)

        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        self._root.geometry(f"{sw}x{sh}+0+0")

        self._root.configure(bg=theme.BG_TRANS)
        self._root.attributes("-transparentcolor", theme.BG_TRANS)

        self._style = ttk.Style(self._root)
        self._style.theme_use("default")
        self._applyTtkStyle()

        self._buildTopBar()
        self._buildPanels()
        self._buildStrengthIndicator(sw, sh)
        self._hideOverlay()

    def _applyTtkStyle(self):
        self._style.configure("TCombobox",
                              fieldbackground=theme.ENTRY_BG, background=theme.BTN_BG,
                              foreground=theme.BTN_FG, arrowcolor=theme.BTN_FG,
                              selectbackground=theme.BTN_BG, selectforeground=theme.BTN_FG)
        self._style.map("TCombobox", fieldbackground=[("readonly", theme.ENTRY_BG)])
        self._root.option_add("*TCombobox*Listbox.background", theme.ENTRY_BG)
        self._root.option_add("*TCombobox*Listbox.foreground", theme.BTN_FG)
        self._root.option_add("*TCombobox*Listbox.selectBackground", theme.BTN_BG)
        self._root.option_add("*TCombobox*Listbox.selectForeground", theme.BTN_FG)

    def _buildTopBar(self):
        self._topBar       = tk.Frame(self._root, bg=theme.BAR_BG)
        self._topBarBorder = tk.Frame(self._root, bg=theme.BORDER_CLR)
        self._tabButtons: dict[int, tk.Button] = {}

        self._addTab("RECOIL",    _TAB_RECOIL,    side="left")
        self._addTab("CROSSHAIR", _TAB_CROSSHAIR, side="left")
        self._addTab("REMAPPER",  _TAB_REMAPPER,  side="left")

        tk.Button(self._topBar, text="QUIT",
                  command=self._root.destroy,
                  bg=theme.BAR_BG, fg="#ff6666", relief="flat",
                  font=("Segoe UI", 9, "bold"), padx=12,
                  activebackground=theme.BTN_BG, activeforeground="#ff6666",
                  cursor="hand2").pack(side="right")
        self._addTab("SETTINGS",  _TAB_SETTINGS,  side="right")
        self._addTab("PROFILES",  _TAB_PROFILES,  side="right")

        self._root.bind("<Left>",  lambda _: self._shiftTab(-1))
        self._root.bind("<Right>", lambda _: self._shiftTab(1))

    def _addTab(self, label: str, index: int, side: str = "left"):
        btn = tk.Button(self._topBar, text=label,
                        command=lambda i=index: self._selectTab(i),
                        bg=theme.BAR_BG, fg=theme.DIM, relief="flat",
                        font=("Segoe UI", 9, "bold"), padx=12,
                        activebackground=theme.BTN_BG, activeforeground=theme.ACCENT,
                        cursor="hand2")
        btn.pack(side=side)
        self._tabButtons[index] = btn

    def _buildPanels(self):
        self._recoilPanel = RecoilPanel(
            self._root, self._settings, self._engine, self._onSettingsChanged)
        self._crosshairPanel = CrosshairPanel(
            self._root, self._settings, self._engine, self._onSettingsChanged)
        self._remapperPanel = RemapperPanel(
            self._root, self._settings, self._onSettingsChanged)
        self._profilesPanel = ProfilesPanel(
            self._root, self._profileData,
            onLoad=self._onProfileLoad,
            onSave=self._onProfileSave,
            onDelete=self._onProfileDelete)
        self._settingsPanel = SettingsPanel(
            self._root, self._settings, self._onSettingsChanged,
            onCapture=self._engine.setSuspendHotkeys,
            onThemeChanged=self._onThemeChanged)

        self._panels = {
            _TAB_RECOIL:    self._recoilPanel,
            _TAB_CROSSHAIR: self._crosshairPanel,
            _TAB_REMAPPER:  self._remapperPanel,
            _TAB_PROFILES:  self._profilesPanel,
            _TAB_SETTINGS:  self._settingsPanel,
        }
        self._tabButtons[_TAB_RECOIL].config(fg=theme.ACCENT, bg=theme.BTN_BG)

    def _buildStrengthIndicator(self, sw: int, sh: int):
        W, H = 35, 24
        x_off = int(sw * 0.02)
        y_off = int(sh * 0.03)
        self._siX = sw // 2 - x_off - W // 2
        self._siY = sh // 2 - y_off - H // 2
        self._siCanvas = tk.Canvas(self._root, width=W, height=H,
                                   bg=theme.BG_TRANS, highlightthickness=0)
        self._siHideId = None
        self._siFont   = ("Segoe UI", 12, "bold")
        self._siCx     = W // 2
        self._siCy     = H // 2

    def _selectTab(self, index: int):
        self._activeTab = index
        for i, btn in self._tabButtons.items():
            btn.config(fg=theme.ACCENT if i == index else theme.DIM,
                       bg=theme.BTN_BG if i == index else theme.BAR_BG)
        for i, panel in self._panels.items():
            if i == index:
                panel.show()
            else:
                panel.hide()

    def _shiftTab(self, direction: int):
        indices = sorted(self._panels.keys())
        current = indices.index(self._activeTab)
        self._selectTab(indices[(current + direction) % len(indices)])

    # ------------------------------------------------------------------
    # Theme rebuild
    # ------------------------------------------------------------------

    def _onThemeChanged(self, name: str):
        theme.setTheme(name)
        self._rebuildUI()

    def _rebuildUI(self):
        was_visible = self._visible
        active_tab  = self._activeTab

        # Destroy topbar and all panels
        self._topBar.destroy()
        self._topBarBorder.destroy()
        self._crosshairPanel._canvas.destroy()
        for panel in self._panels.values():
            panel._border.destroy()

        # Apply updated ttk style
        self._applyTtkStyle()

        # Rebuild
        self._buildTopBar()
        self._buildPanels()

        # Always reopen the overlay after a theme switch so the user sees the result.
        # Reset _visible first so _showOverlay() bypasses its early-return guard.
        self._visible = False
        self._showOverlay()
        self._selectTab(active_tab)

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def _showOverlay(self):
        if self._visible:
            return
        self._visible = True
        self._topBar.place(x=0, y=0, relwidth=1.0, height=theme.TOPBAR_H)
        self._topBarBorder.place(x=0, y=theme.TOPBAR_H, relwidth=1.0, height=2)
        self._selectTab(self._activeTab)
        self._root.focus_force()

    def _hideOverlay(self):
        self._visible = False
        self._topBar.place_forget()
        self._topBarBorder.place_forget()
        for panel in self._panels.values():
            panel.hide()

    def toggleOverlay(self):
        self._root.after(0, self._toggleOverlay)

    def _toggleOverlay(self):
        if self._visible:
            self._hideOverlay()
        else:
            self._showOverlay()

    # ------------------------------------------------------------------
    # Topbar border flash
    # ------------------------------------------------------------------

    def flashBorder(self, color: str):
        try:
            self._topBarBorder.config(bg=color)
            self._root.after(theme.FLASH_MS, lambda: self._topBarBorder.config(bg=theme.BORDER_CLR))
        except tk.TclError:
            pass

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
        new_theme = self._settings.get("theme", "Dark")

        if new_theme != old_theme:
            theme.setTheme(new_theme)
            self._rebuildUI()
        else:
            self._recoilPanel.reload(self._settings)
            self._crosshairPanel.reload(self._settings)
            self._remapperPanel.reload(self._settings)
            self._settingsPanel.reload(self._settings)

        self._profilesPanel.refreshCombo()
        self.flashBorder(theme.FLASH_LOAD)

    def _onProfileSave(self, name: str):
        success = prof.saveProfile(self._profileData, name, self._settings)
        if success:
            self._profilesPanel.refreshCombo()
            self.flashBorder(theme.FLASH_SAVE)

    def _onProfileDelete(self, name: str):
        wasActive = (name == self._profileData.get("active"))
        success = prof.deleteProfile(self._profileData, name)
        if success:
            self._profilesPanel.refreshCombo()
            self.flashBorder(theme.FLASH_DELETE)
            if wasActive:
                settings = prof.loadProfile(self._profileData, prof.DEFAULT_NAME)
                if settings:
                    old_theme = self._settings.get("theme", "Dark")
                    self._engine.updateSettings(settings)
                    for key in settings:
                        self._settings[key] = settings[key]
                    new_theme = self._settings.get("theme", "Dark")
                    if new_theme != old_theme:
                        theme.setTheme(new_theme)
                        self._rebuildUI()
                    else:
                        self._recoilPanel.reload(self._settings)
                        self._crosshairPanel.reload(self._settings)
                        self._remapperPanel.reload(self._settings)
                        self._settingsPanel.reload(self._settings)

    # ------------------------------------------------------------------
    # Engine callbacks
    # ------------------------------------------------------------------

    def quit(self):
        self._root.after(0, self._root.destroy)

    def setEnabled(self, state: bool):
        self._root.after(0, self._applyEnabled, state)

    def onStrengthChanged(self, value: int):
        self._root.after(0, self._recoilPanel.updateStrength, value)
        self._root.after(0, self._showStrengthIndicator, value)

    def _showStrengthIndicator(self, value: int):
        c = self._siCanvas
        cx, cy = self._siCx, self._siCy
        c.delete("all")
        text = str(value)
        for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
            c.create_text(cx + dx, cy + dy, text=text,
                          fill="#000000", font=self._siFont, anchor="center")
        c.create_text(cx, cy, text=text,
                      fill="#ffffff", font=self._siFont, anchor="center")
        c.place(x=self._siX, y=self._siY)
        if self._siHideId is not None:
            self._root.after_cancel(self._siHideId)
        self._siHideId = self._root.after(500, self._hideStrengthIndicator)

    def _hideStrengthIndicator(self):
        self._siCanvas.place_forget()
        self._siHideId = None

    def _applyEnabled(self, state: bool):
        self._settings["recoil"]["enabled"] = state
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        self._root.mainloop()
