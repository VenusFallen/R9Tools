"""Stats overlay configuration panel — imgui implementation."""
from imgui_bundle import imgui
from ui_imgui.base import UIPanel
from stats_poller import lhm_available

_CORNERS = [
    ("top_left",     "Top Left"),
    ("top_right",    "Top Right"),
    ("middle_left",  "Mid Left"),
    ("middle_right", "Mid Right"),
    ("bottom_left",  "Bot Left"),
    ("bottom_right", "Bot Right"),
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
        "enabled": False, "corner": "top_right", "update_rate_hz": 1,
        "show_cpu_usage": True, "show_cpu_temp": True,
        "show_gpu_usage": True, "show_gpu_temp": True,
        "show_gpu_vram": True, "show_ram": True,
        "bg_alpha": 70, "text_color": "#ffffff",
    }


def _hex_to_rgba(h: str) -> tuple:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16)/255, int(h[2:4], 16)/255, int(h[4:6], 16)/255
    return (r, g, b, 1.0)


class StatsUI(UIPanel):

    def __init__(self, settings: dict, on_changed):
        self._settings   = settings
        self._on_changed = on_changed

    def reload(self, settings: dict):
        self._settings = settings
        self._settings.setdefault("stats", _default_stats())["enabled"] = False

    # ------------------------------------------------------------------
    def draw(self):
        s = self._settings.setdefault("stats", _default_stats())

        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Stats Overlay")
        imgui.separator()

        # Enabled
        changed, val = imgui.checkbox("Enabled##st", s.get("enabled", False))
        if changed:
            s["enabled"] = val
            self._on_changed()

        if not lhm_available():
            imgui.spacing()
            imgui.text_colored((0.957, 0.620, 0.043, 1.0),
                               "LHM DLLs not found in lib/.")
            imgui.text_wrapped("Stats require LibreHardwareMonitor.\n"
                               "Install via Settings > Updates > LHM DLLs.")

        imgui.spacing()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "POSITION")
        imgui.separator()

        # Corner grid (2 columns x 3 rows)
        active_corner = s.get("corner", "top_right")
        for row in range(3):
            for col in range(2):
                idx = row * 2 + col
                key, label = _CORNERS[idx]
                if key == active_corner:
                    imgui.push_style_color(imgui.Col_.button,
                                           (0.290, 0.620, 1.000, 1.0))
                if imgui.button(f"{label}##st_c{idx}"):
                    s["corner"] = key
                    self._on_changed()
                if key == active_corner:
                    imgui.pop_style_color()
                if col == 0:
                    imgui.same_line()
            # don't same_line after last col

        imgui.spacing()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "UPDATE RATE")
        imgui.separator()

        changed, val = imgui.slider_int("Hz##st", s.get("update_rate_hz", 1), 1, 5)
        if changed:
            s["update_rate_hz"] = val
            self._on_changed()

        imgui.spacing()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "METRICS")
        imgui.separator()

        for key, label in _METRICS:
            changed, val = imgui.checkbox(f"{label}##st_m", s.get(key, True))
            if changed:
                s[key] = val
                self._on_changed()

        imgui.spacing()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "STYLE")
        imgui.separator()

        changed, val = imgui.slider_int("Opacity %##st", s.get("bg_alpha", 70), 0, 100)
        if changed:
            s["bg_alpha"] = val
            self._on_changed()

        imgui.text("Text Color:")
        imgui.same_line()
        current_col = s.get("text_color", "#ffffff")
        for hex_c, tooltip in _COLOR_PRESETS:
            rgba = _hex_to_rgba(hex_c)
            active = (hex_c == current_col)
            if active:
                imgui.push_style_var(imgui.StyleVar_.frame_border_size, 2.0)
                imgui.push_style_color(imgui.Col_.border, (1.0, 1.0, 1.0, 1.0))
            imgui.push_style_color(imgui.Col_.button,         rgba)
            imgui.push_style_color(imgui.Col_.button_hovered, rgba)
            imgui.push_style_color(imgui.Col_.button_active,  rgba)
            if imgui.button(f"  ##{hex_c}st"):
                s["text_color"] = hex_c
                self._on_changed()
            if imgui.is_item_hovered():
                imgui.set_tooltip(tooltip)
            imgui.pop_style_color(3)
            if active:
                imgui.pop_style_color()
                imgui.pop_style_var()
            imgui.same_line()
        imgui.new_line()
