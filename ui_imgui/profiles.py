"""Profiles panel — imgui implementation."""
from imgui_bundle import imgui
import profiles as prof
from ui_imgui.base import UIPanel

_INPUT_BUF_SIZE = 128


class ProfilesUI(UIPanel):
    right_anchor = True

    def __init__(self, profile_data: dict, on_load, on_save, on_delete):
        self._profile_data = profile_data
        self._on_load      = on_load
        self._on_save      = on_save
        self._on_delete    = on_delete

        names         = prof.profileNames(self._profile_data)
        self._names   = names
        self._sel_idx = 0
        active        = profile_data.get("active", "")
        if active in names:
            self._sel_idx = names.index(active)

        self._name_buf    = ""           # for new-profile name entry

    # ------------------------------------------------------------------

    def refresh_combo(self):
        names = prof.profileNames(self._profile_data)
        self._names = names
        active = self._profile_data.get("active", "")
        if active in names:
            self._sel_idx = names.index(active)
        elif names:
            self._sel_idx = 0

    # ------------------------------------------------------------------
    def draw(self):
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Profiles")
        imgui.separator()

        # --- Active profile selector ---
        imgui.text("Active Profile:")
        imgui.set_next_item_width(-1)
        changed, idx = imgui.combo("##prof_combo", self._sel_idx,
                                   self._names if self._names else ["(none)"])
        if changed:
            self._sel_idx = idx

        imgui.spacing()
        selected_name = self._names[self._sel_idx] if self._names else ""
        active_name   = self._profile_data.get("active", "")

        # Apply / Quick-Save row
        apply_disabled = (selected_name == active_name or not selected_name)
        if apply_disabled:
            imgui.begin_disabled()
        if imgui.button("Apply##prof"):
            self._on_load(selected_name)
        if apply_disabled:
            imgui.end_disabled()

        imgui.same_line()

        save_disabled = (selected_name == prof.DEFAULT_NAME or not selected_name)
        if save_disabled:
            imgui.begin_disabled()
        if imgui.button("Quick Save##prof"):
            self._on_save(selected_name)
        if save_disabled:
            imgui.end_disabled()

        imgui.separator()

        # --- Save / Delete by name ---
        imgui.text("Profile Name:")
        imgui.set_next_item_width(-1)
        _, self._name_buf = imgui.input_text("##prof_name", self._name_buf, _INPUT_BUF_SIZE)

        name = self._name_buf.strip()
        imgui.spacing()

        if imgui.button("Save As##prof"):
            if name:
                self._on_save(name)
                self._name_buf = ""
        imgui.same_line()

        imgui.push_style_color(imgui.Col_.text, (1.0, 0.4, 0.4, 1.0))
        del_disabled = not name
        if del_disabled:
            imgui.begin_disabled()
        if imgui.button("Delete##prof"):
            self._on_delete(name)
            self._name_buf = ""
        if del_disabled:
            imgui.end_disabled()
        imgui.pop_style_color()
