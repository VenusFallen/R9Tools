"""Recoil compensation panel — imgui implementation."""
import threading

from imgui_bundle import imgui
from ui_imgui.base import UIPanel, CaptureHelper, combo_label, binding_label
from recoil import scancodeLabel

_MOUSE_LABELS = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}

_DEFAULT_SLOT = {"key": None, "strength_y": 5, "label": ""}


def _trigger_label(keys: list) -> str:
    parts = [_MOUSE_LABELS.get(k, k.upper()) for k in keys]
    return " + ".join(parts) if parts else "None"


def _slot_key_label(sk: dict) -> str:
    t = sk.get("type", "key")
    if t == "mouse":
        return _MOUSE_LABELS.get(sk.get("button", ""), "?")
    if t == "scroll":
        return "Wheel Up" if sk.get("direction") == "up" else "Wheel Down"
    code = sk.get("code", 0)
    return scancodeLabel(code, sk.get("e0", False)) if code else "(none)"


class RecoilUI(UIPanel):

    def __init__(self, settings: dict, engine, macro_engine, on_changed):
        self._settings     = settings
        self._engine       = engine
        self._macro_engine = macro_engine
        self._on_changed   = on_changed

        # Recoil trigger capture
        self._trig_cap   = CaptureHelper(keyboard_only=False)
        # RF trigger capture
        self._rf_cap     = CaptureHelper(keyboard_only=False)
        # Weapon slot key captures {slot_idx: CaptureHelper}
        self._slot_caps: dict[int, CaptureHelper] = {}

    def reload(self, settings: dict):
        self._settings = settings
        self._settings["recoil"]["enabled"]       = False
        self._settings.get("rapidfire", {})["enabled"] = False
        self._trig_cap  = CaptureHelper(keyboard_only=False)
        self._rf_cap    = CaptureHelper(keyboard_only=False)
        self._slot_caps = {}

    # ------------------------------------------------------------------
    def draw(self):
        s  = self._settings.setdefault("recoil", {
            "enabled": False, "trigger_keys": ["mouse_left"],
            "strength_y": 5, "humanize": False, "slots": [],
        })
        rf = self._settings.setdefault("rapidfire", {
            "enabled": False, "trigger_keys": ["mouse_left"],
            "interval_ms": 100, "humanize": False,
        })

        # ---- Recoil section ----
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Recoil Compensation")

        # Status dot
        r_enabled = s.get("enabled", False)
        state_col = (0.133, 0.769, 0.369, 1.0) if r_enabled else (0.533, 0.533, 0.533, 1.0)
        dl = imgui.get_window_draw_list()
        pos = imgui.get_cursor_screen_pos()
        dl.add_circle_filled((pos.x + 5, pos.y + 8), 5, _rgba_int(*[int(c*255) for c in state_col[:3]]))
        imgui.dummy((14, 14))
        imgui.same_line()
        imgui.text_colored(state_col, "ACTIVE" if r_enabled else "OFF")

        imgui.separator()

        # Enabled toggle
        changed, val = imgui.checkbox("Enabled##rc", r_enabled)
        if changed:
            s["enabled"] = val
            self._on_changed()

        # Humanize
        changed, val = imgui.checkbox("Humanize##rc", s.get("humanize", False))
        if changed:
            s["humanize"] = val
            self._on_changed()

        # Trigger key
        trig_label = _trigger_label(s.get("trigger_keys", ["mouse_left"]))
        if self._trig_cap.capturing:
            trig_label = "Press a key..."
            imgui.push_style_color(imgui.Col_.text, (0.133, 0.769, 0.369, 1.0))
        imgui.text("Trigger:")
        imgui.same_line()
        if imgui.button(trig_label + "##rc_trig"):
            if not self._trig_cap.capturing:
                self._trig_cap = CaptureHelper(keyboard_only=False)
                self._trig_cap.start()
        if self._trig_cap.capturing:
            imgui.pop_style_color()
        if self._trig_cap.is_done():
            result = self._trig_cap.take()
            if result:
                # Build key name from binding dict
                t = result.get("type", "key")
                if t == "mouse":
                    key = result.get("button", "mouse_left")
                elif t == "scroll":
                    key = f"scroll_{result.get('direction','up')}"
                else:
                    key = scancodeLabel(result.get("code", 0), result.get("e0", False))
                s["trigger_keys"] = [key]
                self._on_changed()

        # Strength
        imgui.separator()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "WEAPONS")

        slots = s.setdefault("slots", [])

        # Default / global strength when no slots
        if not slots:
            changed, val = imgui.slider_int("Strength Y##rc", s.get("strength_y", 5), 1, 20)
            if changed:
                s["strength_y"] = val
                self._on_changed()
        else:
            for i, slot in enumerate(slots):
                _label = slot.get("label") or f"Slot {i+1}"
                slot_key = slot.get("key")
                key_lbl  = _slot_key_label(slot_key) if slot_key else "(no key)"

                imgui.push_id(i)

                # Row: [Label]  [Key Btn]  [Strength]  [-]
                imgui.text(_label[:12])
                imgui.same_line(90)

                cap = self._slot_caps.get(i)
                btn_lbl = "Capturing..." if (cap and cap.capturing) else key_lbl
                if imgui.button(btn_lbl + "##sk"):
                    if not (cap and cap.capturing):
                        c = CaptureHelper(keyboard_only=False)
                        c.start()
                        self._slot_caps[i] = c
                if cap and cap.is_done():
                    result = cap.take()
                    if result:
                        slots[i]["key"] = result
                        self._on_changed()
                    self._slot_caps.pop(i, None)

                imgui.same_line()
                imgui.set_next_item_width(80)
                changed, val = imgui.slider_int("##str", slot.get("strength_y", 5), 1, 20)
                if changed:
                    slots[i]["strength_y"] = val
                    self._on_changed()

                imgui.same_line()
                if imgui.button("-##del"):
                    slots.pop(i)
                    self._slot_caps.pop(i, None)
                    self._on_changed()
                    imgui.pop_id()
                    break

                imgui.pop_id()

        if imgui.button("+ Add Weapon"):
            slots.append(dict(_DEFAULT_SLOT))
            self._on_changed()

        imgui.separator()

        # ---- Rapid Fire section ----
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Rapid Fire")
        imgui.same_line()
        changed, val = imgui.checkbox("##rf_en", rf.get("enabled", False))
        if changed:
            rf["enabled"] = val
            self._on_changed()

        rf_col = (0.133, 0.769, 0.369, 1.0) if rf.get("enabled") else (0.533, 0.533, 0.533, 1.0)
        dl2    = imgui.get_window_draw_list()
        p2     = imgui.get_cursor_screen_pos()
        dl2.add_circle_filled((p2.x + 5, p2.y + 8), 5,
                              _rgba_int(*[int(c*255) for c in rf_col[:3]]))
        imgui.dummy((14, 14))
        imgui.same_line()
        imgui.text_colored(rf_col, "ACTIVE" if rf.get("enabled") else "OFF")

        imgui.separator()

        # RF Interval
        changed, val = imgui.slider_int("Interval (ms)##rf", rf.get("interval_ms", 100), 10, 1000)
        if changed:
            rf["interval_ms"] = val
            self._on_changed()

        # RF Humanize
        changed, val = imgui.checkbox("Humanize##rf", rf.get("humanize", False))
        if changed:
            rf["humanize"] = val
            self._on_changed()

        # RF Trigger
        rf_trig_lbl = _trigger_label(rf.get("trigger_keys", ["mouse_left"]))
        if self._rf_cap.capturing:
            rf_trig_lbl = "Press a key..."
            imgui.push_style_color(imgui.Col_.text, (0.133, 0.769, 0.369, 1.0))
        imgui.text("Fire Trigger:")
        imgui.same_line()
        if imgui.button(rf_trig_lbl + "##rf_trig"):
            if not self._rf_cap.capturing:
                self._rf_cap = CaptureHelper(keyboard_only=False)
                self._rf_cap.start()
        if self._rf_cap.capturing:
            imgui.pop_style_color()
        if self._rf_cap.is_done():
            result = self._rf_cap.take()
            if result:
                t = result.get("type", "key")
                if t == "mouse":
                    key = result.get("button", "mouse_left")
                elif t == "scroll":
                    key = f"scroll_{result.get('direction','up')}"
                else:
                    key = scancodeLabel(result.get("code", 0), result.get("e0", False))
                rf["trigger_keys"] = [key]
                self._on_changed()


def _rgba_int(r: int, g: int, b: int, a: int = 255) -> int:
    return imgui.IM_COL32(r, g, b, a)
