"""Stats overlay configuration panel."""
import copy

from PySide6.QtCore    import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

import theme
from panels.base  import Panel
from stats_poller import lhm_available

_CORNERS = [
    ("top_left",     "↖ Top Left"),
    ("top_right",    "↗ Top Right"),
    ("middle_left",  "◀ Mid Left"),
    ("middle_right", "▶ Mid Right"),
    ("bottom_left",  "↙ Bot Left"),
    ("bottom_right", "↘ Bot Right"),
]

_METRICS = [
    ("show_cpu_usage", "CPU Usage"),
    ("show_cpu_temp",  "CPU Temp"),
    ("show_gpu_usage", "GPU Usage"),
    ("show_gpu_temp",  "GPU Temp"),
    ("show_gpu_vram",  "GPU VRAM"),
    ("show_ram",       "RAM"),
]

_COLOR_PRESETS = [
    ("#ffffff", "White"),
    ("#ffff00", "Yellow"),
    ("#00ffff", "Cyan"),
    ("#00ff00", "Green"),
    ("#ff8c00", "Orange"),
    ("#ff4444", "Red"),
]


def _default_stats() -> dict:
    return {
        "enabled":        False,
        "corner":         "top_right",
        "update_rate_hz": 1,
        "show_cpu_usage": True,
        "show_cpu_temp":  True,
        "show_gpu_usage": True,
        "show_gpu_temp":  True,
        "show_gpu_vram":  True,
        "show_ram":       True,
        "bg_alpha":       70,
        "text_color":     "#ffffff",
    }


class StatsPanel(Panel):

    def __init__(self, parent, settings: dict, onSettingsChanged):
        super().__init__(parent)
        self._settings          = settings
        self._onSettingsChanged = onSettingsChanged
        self._build()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        s = self._settings.setdefault("stats", _default_stats())

        title = QLabel("Stats Overlay")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)
        self._layout.addWidget(_sep())

        # Enabled toggle row
        enableRow = QFrame()
        el = QHBoxLayout(enableRow)
        el.setContentsMargins(10, 3, 10, 3)
        el.setSpacing(4)
        elbl = QLabel("Enabled", enableRow)
        elbl.setFixedWidth(120)
        el.addWidget(elbl)
        self._enabledSwitch = theme.ToggleSwitch(
            enableRow, value=s.get("enabled", False), command=self._onToggle)
        el.addWidget(self._enabledSwitch)
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

        self._cornerBtns: dict[str, QPushButton] = {}
        for i, (key, label) in enumerate(_CORNERS):
            btn = QPushButton(label, posCard)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._onCorner(k))
            self._cornerBtns[key] = btn
            rows[i // 2].addWidget(btn)
        self._refreshCornerBtns(s.get("corner", "top_right"))

        # Update rate card
        rateCard = theme.buildCard(self)
        rate_lbl = theme.sectionLabel(rateCard, "Update Rate")
        rateCard.layout().addWidget(rate_lbl)
        self._rateRow = theme.buildPlusMinusRow(
            rateCard, "Hz", s.get("update_rate_hz", 1), 1, 5, self._onRate)

        # Metrics card
        metricsCard = theme.buildCard(self)
        m_lbl = theme.sectionLabel(metricsCard, "Metrics")
        metricsCard.layout().addWidget(m_lbl)

        self._metricSwitches: dict[str, theme.ToggleSwitch] = {}
        for key, label in _METRICS:
            row = QFrame(metricsCard)
            hl  = QHBoxLayout(row)
            hl.setContentsMargins(10, 2, 10, 2)
            hl.setSpacing(4)
            lbl = QLabel(label, row)
            lbl.setFixedWidth(120)
            hl.addWidget(lbl)
            sw = theme.ToggleSwitch(row, value=s.get(key, True),
                                    command=lambda k=key: self._onMetric(k))
            hl.addWidget(sw)
            hl.addStretch()
            metricsCard.layout().addWidget(row)
            self._metricSwitches[key] = sw

        # Style card
        styleCard = theme.buildCard(self)
        style_lbl = theme.sectionLabel(styleCard, "Style")
        styleCard.layout().addWidget(style_lbl)

        self._opacityRow = theme.buildPlusMinusRow(
            styleCard, "Opacity %", s.get("bg_alpha", 70), 0, 100, self._onOpacity)

        colorRow = QFrame(styleCard)
        cl = QHBoxLayout(colorRow)
        cl.setContentsMargins(10, 4, 10, 6)
        cl.setSpacing(5)
        colorLbl = QLabel("Text Color", colorRow)
        colorLbl.setFixedWidth(80)
        cl.addWidget(colorLbl)

        self._colorBtns: dict[str, QPushButton] = {}
        for color, tooltip in _COLOR_PRESETS:
            btn = QPushButton(colorRow)
            btn.setFixedSize(22, 22)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=color: self._onColor(c))
            self._colorBtns[color] = btn
            cl.addWidget(btn)
        cl.addStretch()
        styleCard.layout().addWidget(colorRow)
        self._refreshColorBtns(s.get("text_color", "#ffffff"))

        self._layout.addStretch()

    # ------------------------------------------------------------------
    # Reload (called on profile load)
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        self._settings = settings
        s = settings.setdefault("stats", _default_stats())

        self._enabledSwitch.set(False)
        settings["stats"]["enabled"] = False
        self._refreshCornerBtns(s.get("corner", "top_right"))
        self._rateRow.set(s.get("update_rate_hz", 1))
        for key, sw in self._metricSwitches.items():
            sw.set(s.get(key, True))
        self._opacityRow.set(s.get("bg_alpha", 70))
        self._refreshColorBtns(s.get("text_color", "#ffffff"))

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _onToggle(self):
        self._settings["stats"]["enabled"] = self._enabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onCorner(self, key: str):
        self._settings["stats"]["corner"] = key
        self._refreshCornerBtns(key)
        self._onSettingsChanged(self._settings)

    def _onRate(self):
        self._settings["stats"]["update_rate_hz"] = self._rateRow.get()
        self._onSettingsChanged(self._settings)

    def _onMetric(self, key: str):
        self._settings["stats"][key] = self._metricSwitches[key].get()
        self._onSettingsChanged(self._settings)

    def _onOpacity(self):
        self._settings["stats"]["bg_alpha"] = self._opacityRow.get()
        self._onSettingsChanged(self._settings)

    def _onColor(self, color: str):
        self._settings["stats"]["text_color"] = color
        self._refreshColorBtns(color)
        self._onSettingsChanged(self._settings)

    def _refreshCornerBtns(self, active: str):
        for key, btn in self._cornerBtns.items():
            if key == active:
                btn.setStyleSheet(
                    f"background-color: {theme.ACCENT}; color: #ffffff;")
            else:
                btn.setStyleSheet("")

    def _refreshColorBtns(self, active: str):
        for color, btn in self._colorBtns.items():
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
