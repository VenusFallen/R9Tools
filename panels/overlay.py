"""
Overlay settings panel (UI only).

Consolidates the former Crosshair panel and Stats panel into a single
"Overlay" tab, plus controls for the module status indicators (R / RF
labels) — enabled toggle and screen position.
"""
from PySide6.QtCore    import Qt
from PySide6.QtGui     import QColor
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton

import theme
from panels.base  import Panel
from stats_poller import lhm_available

# ---------------------------------------------------------------------------
# Crosshair constants (from the former panels/crosshair.py)
# ---------------------------------------------------------------------------

_CH_COLOR_PRESETS = [
    ("#00ff00", "Green"),
    ("#ff0000", "Red"),
    ("#ffffff", "White"),
    ("#ff1493", "Pink"),
    ("#ffff00", "Yellow"),
]
# Old profiles stored a named key (e.g. "green") instead of a hex string —
# used by reload() to migrate on load.
_CH_LEGACY_NAME_TO_HEX = {name.lower(): hexval for hexval, name in _CH_COLOR_PRESETS}
_CH_STYLES     = ["Dot", "Cross", "Dot + Cross", "Circle", "Circle + Dot"]
_CH_STYLE_KEYS = ["dot", "cross", "dot_cross", "circle", "circle_dot"]

# ---------------------------------------------------------------------------
# Stats constants (from the former panels/stats.py)
# ---------------------------------------------------------------------------

_STATS_CORNERS = [
    ("top_left",     "↖ Top Left"),
    ("top_right",    "↗ Top Right"),
    ("middle_left",  "◀ Mid Left"),
    ("middle_right", "▶ Mid Right"),
    ("bottom_left",  "↙ Bot Left"),
    ("bottom_right", "↘ Bot Right"),
]

_STATS_METRICS = [
    ("show_fps",       "FPS"),
    ("show_cpu_usage", "CPU Usage"),
    ("show_cpu_temp",  "CPU Temp"),
    ("show_gpu_usage", "GPU Usage"),
    ("show_gpu_temp",  "GPU Temp"),
    ("show_gpu_vram",  "GPU VRAM"),
    ("show_ram",       "RAM"),
]

_STATS_COLOR_PRESETS = [
    ("#ffffff", "White"),
    ("#ffff00", "Yellow"),
    ("#00ffff", "Cyan"),
    ("#00ff00", "Green"),
    ("#ff8c00", "Orange"),
    ("#ff4444", "Red"),
]

_STATS_DEFAULT = {
    "enabled":        False,
    "corner":         "top_right",
    "update_rate_hz": 1,
    "show_fps":       True,
    "show_cpu_usage": True,
    "show_cpu_temp":  True,
    "show_gpu_usage": True,
    "show_gpu_temp":  True,
    "show_gpu_vram":  True,
    "show_ram":       True,
    "bg_alpha":       70,
    "text_color":     "#ffffff",
}

# ---------------------------------------------------------------------------
# Indicator constants (new — module status labels, R / RF)
# ---------------------------------------------------------------------------

_INDICATOR_POSITIONS = [
    ("top_left",        "↖ Top Left"),
    ("top_right",        "↗ Top Right"),
    ("above_crosshair", "▲ Above Crosshair"),
    ("below_crosshair", "▼ Below Crosshair"),
    ("bottom_left",     "↙ Bot Left"),
    ("bottom_right",    "↘ Bot Right"),
]

_INDICATOR_DEFAULT = {
    "enabled":  True,
    "position": "below_crosshair",
}


class OverlayPanel(Panel):
    """Combined Crosshair + Stats + Module Indicator settings."""

    def __init__(self, parent, settings: dict, engine, onSettingsChanged):
        super().__init__(parent)
        self._settings          = settings
        self._engine            = engine
        self._onSettingsChanged = onSettingsChanged
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        title = QLabel("Overlay")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)
        self._layout.addWidget(_sep())

        self._buildCrosshairSection()
        self._layout.addWidget(_sep())
        self._buildIndicatorSection()
        self._layout.addWidget(_sep())
        self._buildStatsSection()

        self._layout.addStretch()

    # ------------------------------------------------------------------
    # Crosshair section
    # ------------------------------------------------------------------

    def _buildCrosshairSection(self):
        s = self._settings["crosshair"]

        sec = theme.sectionLabel(self, "Crosshair")
        self._layout.addWidget(sec)

        # Enabled row
        enableRow = QFrame()
        el = QHBoxLayout(enableRow)
        el.setContentsMargins(10, 3, 10, 3)
        el.setSpacing(4)
        elbl = QLabel("Enabled", enableRow)
        elbl.setFixedWidth(120)
        el.addWidget(elbl)
        self._chEnabledSwitch = theme.ToggleSwitch(
            enableRow, value=s["enabled"], command=self._onChToggle)
        el.addWidget(self._chEnabledSwitch)
        el.addStretch()
        self._layout.addWidget(enableRow)

        # Style + Color card
        card1 = theme.buildCard(self)

        styleRow = QFrame(card1)
        sl = QHBoxLayout(styleRow)
        sl.setContentsMargins(10, 3, 10, 3)
        sl.setSpacing(4)
        slbl = QLabel("Style", styleRow)
        slbl.setFixedWidth(120)
        sl.addWidget(slbl)
        self._chStyleCombo = QComboBox(styleRow)
        self._chStyleCombo.addItems(_CH_STYLES)
        self._chStyleCombo.setCurrentText(self._chKeyToLabel(s["style"]))
        self._chStyleCombo.currentTextChanged.connect(self._onChStyleChange)
        sl.addWidget(self._chStyleCombo)
        sl.addStretch()
        card1.layout().addWidget(styleRow)

        colorRow = QFrame(card1)
        cl = QHBoxLayout(colorRow)
        cl.setContentsMargins(10, 3, 10, 3)
        cl.setSpacing(5)
        clbl = QLabel("Color", colorRow)
        clbl.setFixedWidth(80)
        cl.addWidget(clbl)

        self._chColorBtns: dict[str, QPushButton] = {}
        for color, tooltip in _CH_COLOR_PRESETS:
            btn = QPushButton(colorRow)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=color: self._onChColorChange(c))
            self._chColorBtns[color] = btn
            cl.addWidget(btn)
        cl.addStretch()
        card1.layout().addWidget(colorRow)

        hueRow = QFrame(card1)
        hl = QHBoxLayout(hueRow)
        hl.setContentsMargins(10, 2, 10, 6)
        self._chHueSlider = theme.HueSlider(hueRow)
        self._chHueSlider.hueChanged.connect(self._onChHueChange)
        hl.addWidget(self._chHueSlider)
        card1.layout().addWidget(hueRow)

        self._refreshChColorUI(s["color"])

        # Size parameters card
        card2 = theme.buildCard(self)
        self._chSizeRow    = theme.buildPlusMinusRow(
            card2, "Size",         s["size"],         1,  30, self._onChParamChange)
        self._chThickRow   = theme.buildPlusMinusRow(
            card2, "Thickness",    s["thickness"],    1,  10, self._onChParamChange)
        self._chGapRow     = theme.buildPlusMinusRow(
            card2, "Gap",          s["gap"],          0,  20, self._onChParamChange)
        self._chOutlineRow = theme.buildPlusMinusRow(
            card2, "Outline Size", s["outline_size"], 0,   5, self._onChParamChange)

    # ------------------------------------------------------------------
    # Indicator section
    # ------------------------------------------------------------------

    def _buildIndicatorSection(self):
        s = self._settings.setdefault("indicator", dict(_INDICATOR_DEFAULT))

        sec = theme.sectionLabel(self, "Module Indicators (R / RF)")
        self._layout.addWidget(sec)

        # Enabled row
        enableRow = QFrame()
        el = QHBoxLayout(enableRow)
        el.setContentsMargins(10, 3, 10, 3)
        el.setSpacing(4)
        elbl = QLabel("Enabled", enableRow)
        elbl.setFixedWidth(120)
        el.addWidget(elbl)
        self._indEnabledSwitch = theme.ToggleSwitch(
            enableRow, value=s.get("enabled", True), command=self._onIndToggle)
        el.addWidget(self._indEnabledSwitch)
        el.addStretch()
        self._layout.addWidget(enableRow)

        # Position card
        posCard = theme.buildCard(self)
        pos_lbl = theme.sectionLabel(posCard, "Position")
        posCard.layout().addWidget(pos_lbl)

        rows = []
        for r in range(3):
            row = QFrame(posCard)
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2 if r < 2 else 6)
            hl.setSpacing(6)
            posCard.layout().addWidget(row)
            rows.append(hl)

        self._indPosBtns: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(_INDICATOR_POSITIONS):
            btn = QPushButton(label, posCard)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._onIndPosition(k))
            self._indPosBtns[key] = btn
            rows[i // 2].addWidget(btn)
        self._refreshIndPosBtns(s.get("position", "below_crosshair"))

    # ------------------------------------------------------------------
    # Stats section
    # ------------------------------------------------------------------

    def _buildStatsSection(self):
        s = self._settings.setdefault("stats", dict(_STATS_DEFAULT))

        sec = theme.sectionLabel(self, "Stats")
        self._layout.addWidget(sec)

        # Enabled toggle row
        enableRow = QFrame()
        el = QHBoxLayout(enableRow)
        el.setContentsMargins(10, 3, 10, 3)
        el.setSpacing(4)
        elbl = QLabel("Enabled", enableRow)
        elbl.setFixedWidth(120)
        el.addWidget(elbl)
        self._stEnabledSwitch = theme.ToggleSwitch(
            enableRow, value=s.get("enabled", False), command=self._onStToggle)
        el.addWidget(self._stEnabledSwitch)
        el.addStretch()
        self._layout.addWidget(enableRow)

        # LHM unavailable notice
        if not lhm_available():
            notice = QLabel(
                "LibreHardwareMonitorLib.dll not found in lib/.\n"
                "Stats will not display until it is installed.\n"
                "Download from: github.com/LibreHardwareMonitor/\n"
                "LibreHardwareMonitor/releases")
            notice.setWordWrap(True)
            notice.setStyleSheet(
                f"color: {theme.MINUS_FG}; padding: 6px 10px 6px 10px;"
                f" font-size: 8pt;")
            self._layout.addWidget(notice)

        # Position card
        posCard = theme.buildCard(self)
        pos_lbl = theme.sectionLabel(posCard, "Position")
        posCard.layout().addWidget(pos_lbl)

        rows = []
        for r in range(3):
            row = QFrame(posCard)
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2 if r < 2 else 6)
            hl.setSpacing(6)
            posCard.layout().addWidget(row)
            rows.append(hl)

        self._stCornerBtns: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(_STATS_CORNERS):
            btn = QPushButton(label, posCard)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._onStCorner(k))
            self._stCornerBtns[key] = btn
            rows[i // 2].addWidget(btn)
        self._refreshStCornerBtns(s.get("corner", "top_right"))

        # Update rate card
        rateCard = theme.buildCard(self)
        rate_lbl = theme.sectionLabel(rateCard, "Update Rate")
        rateCard.layout().addWidget(rate_lbl)
        self._stRateRow = theme.buildPlusMinusRow(
            rateCard, "Hz", s.get("update_rate_hz", 1), 1, 5, self._onStRate)

        # Metrics card
        metricsCard = theme.buildCard(self)
        m_lbl = theme.sectionLabel(metricsCard, "Metrics")
        metricsCard.layout().addWidget(m_lbl)

        self._stMetricSwitches: dict[str, theme.ToggleSwitch] = {}
        for key, label in _STATS_METRICS:
            row = QFrame(metricsCard)
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2)
            hl.setSpacing(4)
            lbl = QLabel(label, row)
            lbl.setFixedWidth(120)
            hl.addWidget(lbl)
            sw = theme.ToggleSwitch(row, value=s.get(key, True),
                                    command=lambda k=key: self._onStMetric(k))
            hl.addWidget(sw)
            hl.addStretch()
            metricsCard.layout().addWidget(row)
            self._stMetricSwitches[key] = sw

        # Style card
        styleCard = theme.buildCard(self)
        style_lbl = theme.sectionLabel(styleCard, "Style")
        styleCard.layout().addWidget(style_lbl)

        self._stOpacityRow = theme.buildPlusMinusRow(
            styleCard, "Opacity %", s.get("bg_alpha", 70), 0, 100, self._onStOpacity)

        colorRow = QFrame(styleCard)
        cl = QHBoxLayout(colorRow)
        cl.setContentsMargins(10, 4, 10, 6)
        cl.setSpacing(5)
        colorLbl = QLabel("Text Color", colorRow)
        colorLbl.setFixedWidth(80)
        cl.addWidget(colorLbl)

        self._stColorBtns: dict[str, QPushButton] = {}
        for color, tooltip in _STATS_COLOR_PRESETS:
            btn = QPushButton(colorRow)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=color: self._onStColor(c))
            self._stColorBtns[color] = btn
            cl.addWidget(btn)
        cl.addStretch()
        styleCard.layout().addWidget(colorRow)
        self._refreshStColorBtns(s.get("text_color", "#ffffff"))

    # ------------------------------------------------------------------
    # Reload (called on profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        self._settings = settings

        # Crosshair — purely visual, so unlike recoil/remapper the enabled
        # state carries over from the profile rather than being forced off.
        s = settings["crosshair"]
        self._settings["crosshair"].update(s)

        self._chStyleCombo.blockSignals(True)

        self._chEnabledSwitch.set(s["enabled"])
        self._chStyleCombo.setCurrentText(self._chKeyToLabel(s["style"]))
        self._refreshChColorUI(s["color"])
        self._chSizeRow.set(s["size"])
        self._chThickRow.set(s["thickness"])
        self._chGapRow.set(s["gap"])
        self._chOutlineRow.set(s["outline_size"])

        self._chStyleCombo.blockSignals(False)

        # Indicator — same carry-over rationale as crosshair above.
        ind = settings.setdefault("indicator", dict(_INDICATOR_DEFAULT))
        self._indEnabledSwitch.set(ind.get("enabled", True))
        self._refreshIndPosBtns(ind.get("position", "below_crosshair"))

        # Stats — same carry-over rationale as crosshair above.
        st = settings.setdefault("stats", dict(_STATS_DEFAULT))
        self._stEnabledSwitch.set(st.get("enabled", False))
        self._refreshStCornerBtns(st.get("corner", "top_right"))
        self._stRateRow.set(st.get("update_rate_hz", 1))
        for key, sw in self._stMetricSwitches.items():
            sw.set(st.get(key, True))
        self._stOpacityRow.set(st.get("bg_alpha", 70))
        self._refreshStColorBtns(st.get("text_color", "#ffffff"))

    # ------------------------------------------------------------------
    # Crosshair handlers
    # ------------------------------------------------------------------

    def _onChToggle(self):
        self._settings["crosshair"]["enabled"] = self._chEnabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onChStyleChange(self, label: str):
        self._settings["crosshair"]["style"] = self._chLabelToKey(label)
        self._onSettingsChanged(self._settings)

    def _onChColorChange(self, color_hex: str):
        self._settings["crosshair"]["color"] = color_hex
        self._refreshChColorUI(color_hex)
        self._onSettingsChanged(self._settings)

    def _onChHueChange(self, hue: int):
        self._onChColorChange(QColor.fromHsv(hue, 255, 255).name())

    def _refreshChColorUI(self, color_hex: str):
        if not color_hex.startswith("#"):
            # Old profiles stored a named key (e.g. "green") instead of hex —
            # migrate it and write the hex back so a later save round-trips.
            color_hex = _CH_LEGACY_NAME_TO_HEX.get(color_hex.lower(), _CH_COLOR_PRESETS[0][0])
            self._settings["crosshair"]["color"] = color_hex

        for hexval, btn in self._chColorBtns.items():
            if hexval == color_hex:
                btn.setStyleSheet(
                    f"background-color: {hexval}; border: 2px solid #ffffff;"
                    f" border-radius: 3px;")
            else:
                btn.setStyleSheet(
                    f"background-color: {hexval}; border: 1px solid #444444;"
                    f" border-radius: 3px;")

        # getHsv() returns hue -1 for fully desaturated colors (e.g. white) —
        # leave the slider where it is rather than snapping it to 0.
        hue = QColor(color_hex).getHsv()[0]
        if hue >= 0:
            self._chHueSlider.setHue(hue)

    def _onChParamChange(self):
        s = self._settings["crosshair"]
        s["size"]         = self._chSizeRow.get()
        s["thickness"]    = self._chThickRow.get()
        s["gap"]          = self._chGapRow.get()
        s["outline_size"] = self._chOutlineRow.get()
        self._onSettingsChanged(self._settings)

    def _chKeyToLabel(self, key: str) -> str:
        try:
            return _CH_STYLES[_CH_STYLE_KEYS.index(key)]
        except ValueError:
            return _CH_STYLES[1]

    def _chLabelToKey(self, label: str) -> str:
        try:
            return _CH_STYLE_KEYS[_CH_STYLES.index(label)]
        except ValueError:
            return _CH_STYLE_KEYS[1]

    # ------------------------------------------------------------------
    # Indicator handlers
    # ------------------------------------------------------------------

    def _onIndToggle(self):
        self._settings["indicator"]["enabled"] = self._indEnabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onIndPosition(self, key: str):
        self._settings["indicator"]["position"] = key
        self._refreshIndPosBtns(key)
        self._onSettingsChanged(self._settings)

    def _refreshIndPosBtns(self, active: str):
        for key, btn in self._indPosBtns.items():
            if key == active:
                btn.setStyleSheet(
                    f"background-color: {theme.ACCENT}; color: #ffffff;")
            else:
                btn.setStyleSheet("")

    # ------------------------------------------------------------------
    # Stats handlers
    # ------------------------------------------------------------------

    def _onStToggle(self):
        self._settings["stats"]["enabled"] = self._stEnabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onStCorner(self, key: str):
        self._settings["stats"]["corner"] = key
        self._refreshStCornerBtns(key)
        self._onSettingsChanged(self._settings)

    def _onStRate(self):
        self._settings["stats"]["update_rate_hz"] = self._stRateRow.get()
        self._onSettingsChanged(self._settings)

    def _onStMetric(self, key: str):
        self._settings["stats"][key] = self._stMetricSwitches[key].get()
        self._onSettingsChanged(self._settings)

    def _onStOpacity(self):
        self._settings["stats"]["bg_alpha"] = self._stOpacityRow.get()
        self._onSettingsChanged(self._settings)

    def _onStColor(self, color: str):
        self._settings["stats"]["text_color"] = color
        self._refreshStColorBtns(color)
        self._onSettingsChanged(self._settings)

    def _refreshStCornerBtns(self, active: str):
        for key, btn in self._stCornerBtns.items():
            if key == active:
                btn.setStyleSheet(
                    f"background-color: {theme.ACCENT}; color: #ffffff;")
            else:
                btn.setStyleSheet("")

    def _refreshStColorBtns(self, active: str):
        for color, btn in self._stColorBtns.items():
            if color == active:
                btn.setStyleSheet(
                    f"background-color: {color}; border: 2px solid #ffffff;"
                    f" border-radius: 3px;")
            else:
                btn.setStyleSheet(
                    f"background-color: {color}; border: 1px solid #444444;"
                    f" border-radius: 3px;")


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
