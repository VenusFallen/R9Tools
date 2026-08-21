"""Macros panel — imgui implementation."""
import threading

from imgui_bundle import imgui
from ui_imgui.base import UIPanel, CaptureHelper, input_label
from macro_engine import MacroEngine, actionLabel

_MOUSE_LABELS = {
    "mouse_left": "LMB", "mouse_right": "RMB", "mouse_middle": "MMB",
    "mouse_x1": "Mouse4", "mouse_x2": "Mouse5",
}
_MODE_LABELS = [("once", "ONCE"), ("hold", "HOLD"), ("toggle", "LOOP")]
_INPUT_BUF   = 256


def _trigger_label(trig: dict) -> str:
    t = trig.get("type", "key")
    if t == "mouse":
        return _MOUSE_LABELS.get(trig.get("button", ""), "?")
    if t == "scroll":
        return "Whl Up" if trig.get("direction") == "up" else "Whl Dn"
    code = trig.get("code", 0)
    if not code:
        return "(none)"
    from recoil import scancodeLabel
    return scancodeLabel(code, trig.get("e0", False))


def _default_macro() -> dict:
    return {
        "name": "New Macro",
        "trigger": {"type": "key", "code": 0, "e0": False},
        "mode": "once",
        "actions": [],
        "enabled": True,
    }


class MacrosUI(UIPanel):

    def __init__(self, settings: dict, macro_engine: MacroEngine, on_changed):
        self._settings     = settings
        self._engine       = macro_engine
        self._on_changed   = on_changed
        self._editing_idx  = -1     # -1 = list view
        self._trig_caps: dict[int, CaptureHelper] = {}
        self._name_bufs:  dict[int, str] = {}

    def reload(self, settings: dict):
        self._settings    = settings
        self._editing_idx = -1
        self._trig_caps.clear()
        self._name_bufs.clear()

    # ------------------------------------------------------------------
    def draw(self):
        macros: list = self._settings.setdefault("macros", [])

        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Macros")
        imgui.separator()

        if self._editing_idx >= 0:
            self._draw_editor(macros)
        else:
            self._draw_list(macros)

    # ------------------------------------------------------------------
    def _draw_list(self, macros: list):
        to_delete = -1
        for i, m in enumerate(macros):
            imgui.push_id(i)
            enabled_col = (0.133, 0.769, 0.369, 1.0) if m.get("enabled") else (0.533, 0.533, 0.533, 1.0)
            imgui.text_colored(enabled_col, "● ")
            imgui.same_line()
            imgui.text(m.get("name", f"Macro {i+1}")[:20])
            imgui.same_line()
            imgui.text_colored((0.533, 0.533, 0.533, 1.0),
                               f" [{_trigger_label(m.get('trigger', {}))}]")
            imgui.same_line()
            if imgui.button("Edit##me"):
                self._editing_idx = i
                self._name_bufs[i] = m.get("name", "")
            imgui.same_line()
            imgui.push_style_color(imgui.Col_.text, (1.0, 0.4, 0.4, 1.0))
            if imgui.button("X##md"):
                to_delete = i
            imgui.pop_style_color()
            imgui.pop_id()

        if to_delete >= 0:
            macros.pop(to_delete)
            self._on_changed()

        imgui.spacing()
        if imgui.button("+ Add Macro"):
            macros.append(_default_macro())
            self._editing_idx = len(macros) - 1
            self._name_bufs[self._editing_idx] = "New Macro"
            self._on_changed()

    # ------------------------------------------------------------------
    def _draw_editor(self, macros: list):
        idx = self._editing_idx
        if idx >= len(macros):
            self._editing_idx = -1
            return

        m = macros[idx]

        if imgui.button("< Back##me"):
            self._editing_idx = -1
            return

        imgui.separator()

        # Name
        buf = self._name_bufs.get(idx, m.get("name", ""))
        imgui.set_next_item_width(-1)
        changed, buf = imgui.input_text("##mc_name", buf, _INPUT_BUF)
        if changed:
            self._name_bufs[idx] = buf
            m["name"] = buf
            self._on_changed()

        # Enabled
        changed, val = imgui.checkbox("Enabled##mc", m.get("enabled", True))
        if changed:
            m["enabled"] = val
            self._on_changed()

        # Mode
        cur_mode = m.get("mode", "once")
        for key, label in _MODE_LABELS:
            active = (cur_mode == key)
            if active:
                imgui.push_style_color(imgui.Col_.button, (0.290, 0.620, 1.000, 0.4))
            if imgui.button(label + "##mc_mode"):
                m["mode"] = key
                self._on_changed()
            if active:
                imgui.pop_style_color()
            imgui.same_line()
        imgui.new_line()

        # Trigger
        trig = m.setdefault("trigger", {"type": "key", "code": 0, "e0": False})
        cap  = self._trig_caps.get(idx)
        if cap and cap.capturing:
            btn_lbl = "Press a key..."
            imgui.push_style_color(imgui.Col_.text, (0.133, 0.769, 0.369, 1.0))
        else:
            btn_lbl = _trigger_label(trig)
        imgui.text("Trigger:")
        imgui.same_line()
        if imgui.button(btn_lbl + "##mc_trig"):
            if not (cap and cap.capturing):
                c = CaptureHelper(keyboard_only=False)
                c.start()
                self._trig_caps[idx] = c
        if cap and cap.capturing:
            imgui.pop_style_color()
        if cap and cap.is_done():
            result = cap.take()
            if result:
                m["trigger"] = result
                self._on_changed()
            self._trig_caps.pop(idx, None)

        imgui.separator()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "ACTIONS")

        # Action list (display only; full editing is complex — show summary)
        actions: list = m.setdefault("actions", [])
        to_del = -1
        for j, action in enumerate(actions):
            imgui.push_id(j)
            imgui.text(f"  {j+1}. {actionLabel(action)[:36]}")
            imgui.same_line()
            if imgui.button("X##act_del"):
                to_del = j
            imgui.pop_id()
        if to_del >= 0:
            actions.pop(to_del)
            self._on_changed()

        if imgui.button("+ Key Press##act"):
            actions.append({"type": "key", "code": 0, "e0": False,
                            "down": True, "up": True, "delay_ms": 0})
            self._on_changed()
        imgui.same_line()
        if imgui.button("+ Delay##act"):
            actions.append({"type": "delay", "ms": 50})
            self._on_changed()
        imgui.same_line()
        if imgui.button("+ Click##act"):
            actions.append({"type": "mouse_click", "button": "mouse_left", "delay_ms": 0})
            self._on_changed()
