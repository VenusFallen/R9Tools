"""Crosshair settings panel — imgui implementation."""
from imgui_bundle import imgui
from ui_imgui.base import UIPanel


_STYLES     = ["Dot", "Cross", "Dot + Cross", "Circle", "Circle + Dot"]
_STYLE_KEYS = ["dot", "cross", "dot_cross", "circle", "circle_dot"]

_COLORS     = ["Green", "Red", "White", "Pink", "Yellow"]
_COLOR_KEYS = ["green", "red", "white", "pink", "yellow"]


class CrosshairUI(UIPanel):

    def __init__(self, settings: dict, engine, on_changed):
        self._settings   = settings
        self._engine     = engine
        self._on_changed = on_changed

    def reload(self, settings: dict):
        self._settings = settings
        self._settings["crosshair"]["enabled"] = False

    # ------------------------------------------------------------------
    def draw(self):
        s = self._settings.setdefault("crosshair", {
            "enabled": False, "style": "cross", "color": "green",
            "size": 10, "thickness": 2, "gap": 4, "outline_size": 1,
        })

        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Crosshair")
        imgui.separator()

        # Enabled
        changed, val = imgui.checkbox("Enabled##ch", s.get("enabled", False))
        if changed:
            s["enabled"] = val
            self._on_changed()

        imgui.spacing()

        # Style
        cur_style = _STYLE_KEYS.index(s.get("style", "cross"))
        changed, idx = imgui.combo("Style##ch", cur_style, _STYLES)
        if changed:
            s["style"] = _STYLE_KEYS[idx]
            self._on_changed()

        # Color
        cur_color = _COLOR_KEYS.index(s.get("color", "green"))
        changed, idx = imgui.combo("Color##ch", cur_color, _COLORS)
        if changed:
            s["color"] = _COLOR_KEYS[idx]
            self._on_changed()

        imgui.separator()

        # Size parameters
        for key, label, lo, hi in [
            ("size",         "Size",         1, 30),
            ("thickness",    "Thickness",    1, 10),
            ("gap",          "Gap",          0, 20),
            ("outline_size", "Outline Size", 0,  5),
        ]:
            changed, val = imgui.slider_int(f"{label}##ch", s.get(key, 1), lo, hi)
            if changed:
                s[key] = val
                self._on_changed()
