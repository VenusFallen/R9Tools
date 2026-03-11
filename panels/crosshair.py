"""
Crosshair settings panel (UI only).
Phase 5 adds crosshair drawing + window-filter polling to overlay_window.py.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel

import theme
from panels.base import Panel


class CrosshairPanel(Panel):

    COLORS = {
        "green":  "#00ff00",
        "red":    "#ff0000",
        "white":  "#ffffff",
        "pink":   "#ff1493",
        "yellow": "#ffff00",
    }
    STYLES     = ["Dot", "Cross", "Dot + Cross", "Circle", "Circle + Dot"]
    STYLE_KEYS = ["dot", "cross", "dot_cross", "circle", "circle_dot"]

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
        s = self._settings["crosshair"]

        title = QLabel("Crosshair")
        title.setStyleSheet(
            f"color: {theme.ACCENT}; font: bold 10pt 'Segoe UI';"
            f" padding: 8px 10px 2px 10px;")
        self._layout.addWidget(title)

        self._layout.addWidget(_sep())

        # Enabled row
        enableRow = QFrame()
        el = QHBoxLayout(enableRow)
        el.setContentsMargins(10, 3, 10, 3)
        el.setSpacing(4)
        elbl = QLabel("Enabled", enableRow)
        elbl.setFixedWidth(120)
        el.addWidget(elbl)
        self._enabledSwitch = theme.ToggleSwitch(
            enableRow, value=s["enabled"], command=self._onToggle)
        el.addWidget(self._enabledSwitch)
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
        self._styleCombo = QComboBox(styleRow)
        self._styleCombo.addItems(self.STYLES)
        self._styleCombo.setCurrentText(self._keyToLabel(s["style"]))
        self._styleCombo.currentTextChanged.connect(self._onStyleChange)
        sl.addWidget(self._styleCombo)
        sl.addStretch()
        card1.layout().addWidget(styleRow)

        colorRow = QFrame(card1)
        cl = QHBoxLayout(colorRow)
        cl.setContentsMargins(10, 3, 10, 3)
        cl.setSpacing(4)
        clbl = QLabel("Color", colorRow)
        clbl.setFixedWidth(120)
        cl.addWidget(clbl)
        self._colorCombo = QComboBox(colorRow)
        self._colorCombo.addItems([c.capitalize() for c in self.COLORS])
        self._colorCombo.setCurrentText(s["color"].capitalize())
        self._colorCombo.currentTextChanged.connect(self._onColorChange)
        cl.addWidget(self._colorCombo)
        cl.addStretch()
        card1.layout().addWidget(colorRow)

        # Size parameters card
        card2 = theme.buildCard(self)
        self._sizeRow    = theme.buildPlusMinusRow(
            card2, "Size",         s["size"],         1,  30, self._onParamChange)
        self._thickRow   = theme.buildPlusMinusRow(
            card2, "Thickness",    s["thickness"],    1,  10, self._onParamChange)
        self._gapRow     = theme.buildPlusMinusRow(
            card2, "Gap",          s["gap"],          0,  20, self._onParamChange)
        self._outlineRow = theme.buildPlusMinusRow(
            card2, "Outline Size", s["outline_size"], 0,   5, self._onParamChange)

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload(self, settings: dict):
        s = settings["crosshair"]
        self._settings["crosshair"].update(s)

        self._styleCombo.blockSignals(True)
        self._colorCombo.blockSignals(True)

        self._enabledSwitch.set(False)
        self._settings["crosshair"]["enabled"] = False
        self._styleCombo.setCurrentText(self._keyToLabel(s["style"]))
        self._colorCombo.setCurrentText(s["color"].capitalize())
        self._sizeRow.set(s["size"])
        self._thickRow.set(s["thickness"])
        self._gapRow.set(s["gap"])
        self._outlineRow.set(s["outline_size"])

        self._styleCombo.blockSignals(False)
        self._colorCombo.blockSignals(False)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _onToggle(self):
        self._settings["crosshair"]["enabled"] = self._enabledSwitch.get()
        self._onSettingsChanged(self._settings)

    def _onStyleChange(self, label: str):
        self._settings["crosshair"]["style"] = self._labelToKey(label)
        self._onSettingsChanged(self._settings)

    def _onColorChange(self, label: str):
        self._settings["crosshair"]["color"] = label.lower()
        self._onSettingsChanged(self._settings)

    def _onParamChange(self):
        s = self._settings["crosshair"]
        s["size"]         = self._sizeRow.get()
        s["thickness"]    = self._thickRow.get()
        s["gap"]          = self._gapRow.get()
        s["outline_size"] = self._outlineRow.get()
        self._onSettingsChanged(self._settings)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _keyToLabel(self, key: str) -> str:
        try:
            return self.STYLES[self.STYLE_KEYS.index(key)]
        except ValueError:
            return self.STYLES[1]

    def _labelToKey(self, label: str) -> str:
        try:
            return self.STYLE_KEYS[self.STYLES.index(label)]
        except ValueError:
            return self.STYLE_KEYS[1]


def _sep():
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setFixedHeight(1)
    s.setStyleSheet(f"background-color: {theme.PANEL_BORDER};")
    return s
