"""Button Remapper panel — imgui implementation."""
from imgui_bundle import imgui
from ui_imgui.base import UIPanel, CaptureHelper, input_label

_INPUT_TYPES = ["key", "mouse", "scroll"]

_MOUSE_OUTPUTS = [
    "mouse_left", "mouse_right", "mouse_middle", "mouse_x1", "mouse_x2",
]
_MOUSE_LABELS = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}


def _default_mapping() -> dict:
    return {
        "from": {"type": "key", "code": 0, "e0": False},
        "to":   {"type": "key", "code": 0, "e0": False},
        "mode": "remap",
    }


class RemapperUI(UIPanel):

    def __init__(self, settings: dict, on_changed):
        self._settings   = settings
        self._on_changed = on_changed
        # {(mapping_idx, "from"|"to"): CaptureHelper}
        self._caps: dict[tuple, CaptureHelper] = {}

    def reload(self, settings: dict):
        self._settings = settings
        self._settings["remapper"]["enabled"] = False
        self._caps.clear()

    # ------------------------------------------------------------------
    def draw(self):
        rm = self._settings.setdefault("remapper", {"enabled": False, "mappings": []})
        mappings: list = rm.setdefault("mappings", [])

        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Button Remapper")
        imgui.separator()

        changed, val = imgui.checkbox("Enabled##rm", rm.get("enabled", False))
        if changed:
            rm["enabled"] = val
            self._on_changed()

        imgui.spacing()

        to_delete = -1
        for i, mapping in enumerate(mappings):
            imgui.push_id(i)
            imgui.separator()

            frm = mapping.setdefault("from", {"type": "key", "code": 0, "e0": False})
            to  = mapping.setdefault("to",   {"type": "key", "code": 0, "e0": False})

            # FROM
            frm_cap = self._caps.get((i, "from"))
            if frm_cap and frm_cap.capturing:
                imgui.text_colored((0.133, 0.769, 0.369, 1.0), "Press any key/button...")
            else:
                frm_lbl = input_label(frm) or "(unbound)"
                imgui.text("From:")
                imgui.same_line()
                if imgui.button(frm_lbl + "##frm"):
                    c = CaptureHelper(keyboard_only=False)
                    c.start()
                    self._caps[(i, "from")] = c
            if frm_cap and frm_cap.is_done():
                result = frm_cap.take()
                if result:
                    mapping["from"] = result
                    self._on_changed()
                self._caps.pop((i, "from"), None)

            imgui.same_line(0, 16)

            # TO
            to_cap = self._caps.get((i, "to"))
            if to_cap and to_cap.capturing:
                imgui.text_colored((0.133, 0.769, 0.369, 1.0), "Press...")
            else:
                to_lbl = input_label(to) or "(unbound)"
                imgui.text("To:")
                imgui.same_line()
                if imgui.button(to_lbl + "##to"):
                    c = CaptureHelper(keyboard_only=False)
                    c.start()
                    self._caps[(i, "to")] = c
            if to_cap and to_cap.is_done():
                result = to_cap.take()
                if result:
                    mapping["to"] = result
                    self._on_changed()
                self._caps.pop((i, "to"), None)

            imgui.same_line(0, 16)
            imgui.push_style_color(imgui.Col_.text, (1.0, 0.4, 0.4, 1.0))
            if imgui.button("X##del"):
                to_delete = i
            imgui.pop_style_color()

            imgui.pop_id()

        if to_delete >= 0:
            mappings.pop(to_delete)
            # Remove stale captures
            self._caps = {k: v for k, v in self._caps.items() if k[0] != to_delete}
            self._on_changed()

        imgui.spacing()
        if imgui.button("+ Add Mapping"):
            mappings.append(_default_mapping())
            self._on_changed()
