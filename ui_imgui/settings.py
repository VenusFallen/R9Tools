"""Settings panel — imgui implementation (hotkeys, theme, window filter, updates)."""
import sys
import threading
import webbrowser

import psutil

from imgui_bundle import imgui
import updater
from ui_imgui.base import UIPanel, CaptureHelper, binding_label
from version import APP_VERSION

try:
    import psutil as _psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False


def _get_processes() -> list[str]:
    if not _PSUTIL:
        return []
    try:
        return sorted(set(
            p.info["name"] for p in psutil.process_iter(["name"])
            if p.info.get("name")
        ))
    except Exception:
        return []


class SettingsUI(UIPanel):
    right_anchor = True

    def __init__(self, settings: dict, engine, on_changed,
                 on_theme_changed, on_capture_suspend, on_quit):
        self._settings          = settings
        self._engine            = engine
        self._on_changed        = on_changed
        self._on_theme_changed  = on_theme_changed
        self._on_suspend        = on_capture_suspend
        self._on_quit           = on_quit

        self._captures: dict[str, CaptureHelper] = {}
        self._proc_list: list[str]  = [""] + _get_processes()

        # Updater state
        self._app_state     = "idle"
        self._app_status    = f"v{APP_VERSION}"
        self._app_btn_label = "Check"
        self._app_btn_en    = True

    def reload(self, settings: dict):
        self._settings = settings
        self._captures.clear()

    # ------------------------------------------------------------------
    def draw(self):
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Settings")
        imgui.separator()

        # ---- Window Filter ----
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "WINDOW FILTER")
        imgui.text_wrapped("Restrict all active modules to the selected process.")

        imgui.spacing()
        cur_filter = self._settings.get("window_filter", "")
        cur_idx    = 0
        if cur_filter in self._proc_list:
            cur_idx = self._proc_list.index(cur_filter)

        imgui.set_next_item_width(imgui.get_content_region_avail().x - 34)
        changed, idx = imgui.combo("##wf", cur_idx,
                                   [p if p else "(none)" for p in self._proc_list])
        if changed:
            self._settings["window_filter"] = self._proc_list[idx]
            self._on_changed()
        imgui.same_line()
        if imgui.button("##wf_refresh"):
            self._proc_list = [""] + _get_processes()
        if imgui.is_item_hovered():
            imgui.set_tooltip("Refresh process list")

        imgui.separator()

        # ---- Theme ----
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "THEME")
        cur_theme = self._settings.get("theme", "Dark")
        for name in ("Dark", "Light"):
            active = (name == cur_theme)
            if active:
                imgui.push_style_color(imgui.Col_.button,
                                       (0.290, 0.620, 1.000, 0.35))
            if imgui.button(name + "##theme"):
                self._settings["theme"] = name
                self._on_theme_changed(name)
                self._on_changed()
            if active:
                imgui.pop_style_color()
            imgui.same_line()
        imgui.new_line()
        imgui.separator()

        # ---- Hotkeys ----
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Hotkeys")
        imgui.separator()
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "GENERAL")

        hk = self._settings.setdefault("hotkeys", {})
        self._hotkey_row("Menu Toggle",   "overlay_toggle",       hk, keyboard_only=True)
        self._hotkey_row("Quit",          "quit",                  hk, keyboard_only=True)

        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "RECOIL")
        self._hotkey_row("Toggle",        "recoil_toggle",         hk, keyboard_only=True)
        self._hotkey_row("Strength +",    "recoil_strength_up",    hk, keyboard_only=True)
        self._hotkey_row("Strength -",    "recoil_strength_down",  hk, keyboard_only=True)

        imgui.separator()

        # ---- Updates ----
        imgui.text_colored((0.290, 0.620, 1.000, 1.0), "Updates")
        imgui.separator()

        # R9Tools
        imgui.text_colored((0.533, 0.533, 0.533, 1.0), "R9TOOLS")
        imgui.text(self._app_status)
        imgui.same_line()
        if not self._app_btn_en:
            imgui.begin_disabled()
        if imgui.button(self._app_btn_label + "##app_upd"):
            self._app_btn_clicked()
        if not self._app_btn_en:
            imgui.end_disabled()

        # Manual fallback: always-visible link to the GitHub releases page,
        # independent of the Check/Update/Restart flow above. Never calls
        # into updater.py and always points at the general releases page.
        if imgui.button("View on GitHub##app_releases"):
            webbrowser.open("https://github.com/VenusFallen/R9Tools/releases")

    # ------------------------------------------------------------------
    # Hotkey row helper
    # ------------------------------------------------------------------

    def _hotkey_row(self, label: str, hk_key: str, hk: dict,
                    keyboard_only: bool = True):
        binding = hk.get(hk_key, {"code": 0, "e0": False})
        cap     = self._captures.get(hk_key)

        if cap and cap.capturing:
            btn_text = "Press a key..."
            imgui.push_style_color(imgui.Col_.text, (0.133, 0.769, 0.369, 1.0))
        else:
            btn_text = binding_label(binding)

        imgui.text(label)
        imgui.same_line(imgui.get_content_region_avail().x - 100 +
                        imgui.get_cursor_pos_x())
        if imgui.button(btn_text + f"##{hk_key}"):
            if not (cap and cap.capturing):
                c = CaptureHelper(keyboard_only=keyboard_only)
                c.start(on_suspend=self._on_suspend)
                self._captures[hk_key] = c

        if cap and cap.capturing:
            imgui.pop_style_color()

        if cap and cap.is_done():
            result = cap.take()
            if result:
                hk[hk_key] = result
                self._on_changed()
            self._captures.pop(hk_key, None)

    # ------------------------------------------------------------------
    # Updater
    # ------------------------------------------------------------------

    def _app_btn_clicked(self):
        if self._app_state in ("idle", "up_to_date", "error"):
            self._start_check_app()
        elif self._app_state == "available":
            self._start_download_app()
        elif self._app_state == "ready":
            self._do_restart()

    def _start_check_app(self):
        self._app_state, self._app_status = "checking", "Checking..."
        self._app_btn_label, self._app_btn_en = "...", False
        threading.Thread(target=self._do_check_app, daemon=True).start()

    def _do_check_app(self):
        try:
            avail, latest = updater.check_app_update(APP_VERSION)
            if avail:
                self._app_state, self._app_status = "available", f"v{latest} available"
                self._app_btn_label, self._app_btn_en = "Update", True
            else:
                self._app_state, self._app_status = "up_to_date", "Up to date"
                self._app_btn_label, self._app_btn_en = "Check", True
        except Exception:
            self._app_state, self._app_status = "error", "Check failed"
            self._app_btn_label, self._app_btn_en = "Retry", True

    def _start_download_app(self):
        if not getattr(sys, "frozen", False):
            self._app_state, self._app_status = "idle", "Dev build — skipped"
            self._app_btn_label, self._app_btn_en = "Check", True
            return
        self._app_state, self._app_status = "downloading", "Downloading..."
        self._app_btn_label, self._app_btn_en = "...", False
        threading.Thread(target=self._do_download_app, daemon=True).start()

    def _do_download_app(self):
        try:
            def prog(pct):
                self._app_status = f"Downloading... {pct}%"
            updater.download_app(prog)
            self._app_state = "ready"
            self._app_status = "Ready — restart to apply"
            self._app_btn_label, self._app_btn_en = "Restart Now", True
        except Exception:
            self._app_state = "error"
            self._app_status = "Download failed"
            self._app_btn_label, self._app_btn_en = "Retry", True

    def _do_restart(self):
        updater.restart_app()
        self._on_quit()
