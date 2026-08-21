"""
R9Tools imgui + Direct3D 11 overlay.

Single full-screen TOPMOST window with per-pixel alpha (DXGI premultiplied)
and WS_EX_NOREDIRECTIONBITMAP — DWM can schedule it as a hardware overlay plane,
preserving Independent Flip / MPO for the foreground game and eliminating the
196→147 FPS regression caused by WS_EX_LAYERED GDI redirection surfaces.

The window uses WM_NCHITTEST for selective click-through: the game area returns
HTTRANSPARENT (mouse events pass to the game), the panel area returns HTCLIENT
(mouse events reach imgui). No WS_EX_TRANSPARENT toggle needed.

Threading:
  - Main thread: render loop (Win32 message pump + imgui frame)
  - Engine thread: RecoilEngine (unchanged)
  - Poller thread: StatsPoller (unchanged)
  - Communication: _AppQueue (thread-safe queue.Queue, replaces UIBridge signals)
"""
import ctypes
import ctypes.wintypes as wintypes
import queue
import time
from ctypes import c_int, c_uint, c_void_p, byref

from imgui_bundle import imgui

import dx11_bridge as dx11
from imgui_backend import ImguiBackend
import dcomp_bridge as dcomp
import profiles as prof
from stats_poller import lhm_available

# Panel tab constants — mirror the Qt version
_TAB_RECOIL    = 0
_TAB_CROSSHAIR = 1
_TAB_REMAPPER  = 2
_TAB_MACROS    = 3
_TAB_STATS     = 4
_TAB_PROFILES  = 5
_TAB_SETTINGS  = 6

_RIGHT_ANCHOR_TABS = {_TAB_PROFILES, _TAB_SETTINGS}

# Layout constants (match theme.py values)
_TOPBAR_H       = 36
_TOPBAR_MARGIN_TOP    = 8
_TOPBAR_MARGIN_SIDE   = 12
_TOPBAR_MARGIN_BOTTOM = 4
_TOPBAR_TOTAL   = _TOPBAR_MARGIN_TOP + _TOPBAR_H + _TOPBAR_MARGIN_BOTTOM  # 48
_PANEL_W        = 260

# Win32 constants
_WS_EX_NOREDIRECTIONBITMAP = 0x00200000
_WS_EX_LAYERED     = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE  = 0x08000000
_WS_EX_TOPMOST     = 0x00000008
_WS_EX_TOOLWINDOW  = 0x00000080
_WS_POPUP          = 0x80000000
_WS_VISIBLE        = 0x10000000
_GWL_EXSTYLE       = -20
_WM_QUIT           = 0x0012
_WM_DESTROY        = 0x0002
_WM_SIZE           = 0x0005
_WM_NCHITTEST      = 0x0084
_WM_CLOSE          = 0x0010
_HTTRANSPARENT     = -1
_HTCLIENT          = 1
_CS_HREDRAW        = 0x0002
_CS_VREDRAW        = 0x0001

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# 64-bit GetWindowLongPtr / SetWindowLongPtr
_user32.GetWindowLongPtrW.restype  = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, c_int]
_user32.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, c_int, ctypes.c_ssize_t]

# Window region helpers for click-through (reliable with DComp windows)
_gdi32 = ctypes.windll.gdi32
_gdi32.CreateRectRgn.restype  = wintypes.HANDLE
_gdi32.CreateRectRgn.argtypes = [c_int, c_int, c_int, c_int]
_gdi32.CombineRgn.restype     = c_int
_gdi32.CombineRgn.argtypes    = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, c_int]
_gdi32.DeleteObject.restype   = wintypes.BOOL
_gdi32.DeleteObject.argtypes  = [wintypes.HANDLE]
_user32.SetWindowRgn.restype  = c_int
_user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
_RGN_OR = 2

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_CH_RGB = {
    "green":  (0,   255, 0),
    "red":    (255, 0,   0),
    "white":  (255, 255, 255),
    "pink":   (255, 20,  147),
    "yellow": (255, 255, 0),
}


def _col32(r: int, g: int, b: int, a: int = 255) -> int:
    return imgui.IM_COL32(r, g, b, a)


def _hex_col32(hex_str: str, alpha: int = 255) -> int:
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return _col32(r, g, b, alpha)


def _ch_col32(name: str, alpha: int = 255) -> int:
    rgb = _CH_RGB.get(name, (0, 255, 0))
    return _col32(*rgb, alpha)


def _shade_col32(shade: int, alpha: int = 255) -> int:
    return _col32(shade, shade, shade, alpha)


# ---------------------------------------------------------------------------
# Win32 WNDCLASSEXW + window creation
# ---------------------------------------------------------------------------

# x64: WPARAM/LPARAM/LRESULT are 64-bit; wintypes defines them as 32-bit → OverflowError
_WPARAM64  = ctypes.c_uint64
_LPARAM64  = ctypes.c_int64
_LRESULT64 = ctypes.c_int64

_WNDPROC_T = ctypes.WINFUNCTYPE(
    _LRESULT64,
    wintypes.HWND, wintypes.UINT, _WPARAM64, _LPARAM64,
)

_DefWindowProcW = _user32.DefWindowProcW
_DefWindowProcW.restype  = _LRESULT64
_DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, _WPARAM64, _LPARAM64]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",        wintypes.UINT),
        ("style",         wintypes.UINT),
        ("lpfnWndProc",   _WNDPROC_T),
        ("cbClsExtra",    c_int),
        ("cbWndExtra",    c_int),
        ("hInstance",     wintypes.HMODULE),
        ("hIcon",         wintypes.HANDLE),
        ("hCursor",       wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName",  wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm",       wintypes.HANDLE),
    ]


# ---------------------------------------------------------------------------
# Thread-safe queue that the engine/poller threads push events into
# ---------------------------------------------------------------------------

class _AppQueue:
    def __init__(self):
        self._q = queue.Queue()

    def put_overlay_toggled(self):
        self._q.put(("overlay", None))

    def put_recoil_toggled(self, state: bool):
        self._q.put(("recoil", state))

    def put_strength_changed(self, value: int):
        self._q.put(("strength", value))

    def put_stats_updated(self, data: dict):
        self._q.put(("stats", data))

    def put_quit(self):
        self._q.put(("quit", None))

    def drain(self, app: "OverlayApp"):
        while True:
            try:
                kind, data = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "overlay":
                app._toggle_overlay()
            elif kind == "recoil":
                app._recoil_enabled = data
            elif kind == "strength":
                app._si_value = data
                app._si_until = time.monotonic() + 0.5
            elif kind == "stats":
                app._stats_data = data
            elif kind == "quit":
                app._running = False


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class OverlayApp:

    def __init__(self, settings: dict, profile_data: dict,
                 engine, macro_engine, on_settings_changed):
        self._settings          = settings
        self._profile_data      = profile_data
        self._engine            = engine
        self._macro_engine      = macro_engine
        self._on_settings_changed = on_settings_changed

        # UI state
        self._running           = True
        self._panel_visible     = False
        self._panel_collapsed   = False
        _valid = {_TAB_RECOIL, _TAB_CROSSHAIR, _TAB_REMAPPER,
                  _TAB_MACROS, _TAB_STATS, _TAB_PROFILES, _TAB_SETTINGS}
        saved = profile_data.get("last_tab", _TAB_RECOIL)
        self._active_tab        = saved if saved in _valid else _TAB_RECOIL
        self._recoil_enabled    = False
        self._stats_data: dict  = {}
        self._si_value: int | None = None
        self._si_until: float   = 0.0
        self._flash_col: int    = 0
        self._flash_until: float = 0.0
        self._panel_h: float    = 400.0   # tracked from previous frame

        # Capture state
        self._capturing_key: bool   = False
        self._capture_callback      = None

        # DX11 + DComp handles (set in _setup_dx11)
        self._hwnd          = 0
        self._device        = 0
        self._context       = 0
        self._swap_chain    = 0
        self._rtv           = 0
        self._dcomp_device  = 0
        self._dcomp_target  = 0
        self._dcomp_visual  = 0
        self._screen_w      = 0
        self._screen_h      = 0

        # imgui backend
        self._backend: ImguiBackend | None = None
        self._dark_theme: bool = True

        # Keep WndProc callback alive (GC guard)
        self._wndproc_cb  = None

        # Queue used by engine/poller threads
        self.queue        = _AppQueue()

        # Lazy-import panel modules (avoid circular deps)
        self._panels      = {}   # filled in _setup_panels

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self):
        self._screen_w = _user32.GetSystemMetrics(0)
        self._screen_h = _user32.GetSystemMetrics(1)

        self._create_window()
        self._setup_dx11()
        self._setup_imgui()
        self._setup_panels()

        self._main_loop()
        self._cleanup()

    # ------------------------------------------------------------------
    # Window creation
    # ------------------------------------------------------------------

    def _create_window(self):
        hinstance = _kernel32.GetModuleHandleW(None)
        class_name = "R9ToolsDX11Overlay"

        wndproc = _WNDPROC_T(self._wndproc)
        self._wndproc_cb = wndproc           # keep reference

        wc = _WNDCLASSEXW()
        wc.cbSize        = ctypes.sizeof(_WNDCLASSEXW)
        wc.style         = _CS_HREDRAW | _CS_VREDRAW
        wc.lpfnWndProc   = wndproc
        wc.hInstance     = hinstance
        wc.lpszClassName = class_name
        _user32.RegisterClassExW(byref(wc))

        ex_style = (
            _WS_EX_TOPMOST
            | _WS_EX_NOACTIVATE          # never steal focus from the game
            | _WS_EX_TOOLWINDOW          # no taskbar button
            | _WS_EX_NOREDIRECTIONBITMAP # DComp owns the surface — no GDI backing
        )

        self._hwnd = _user32.CreateWindowExW(
            ex_style,
            class_name,
            "R9Tools",
            _WS_POPUP | _WS_VISIBLE,
            0, 0, self._screen_w, self._screen_h,
            None, None, hinstance, None,
        )
        if not self._hwnd:
            raise OSError(f"CreateWindowEx failed: {ctypes.get_last_error()}")

        _user32.ShowWindow(self._hwnd, 1)   # SW_SHOWNORMAL
        _user32.UpdateWindow(self._hwnd)

        # Panel starts hidden — make window fully click-through from the start
        self._set_click_through(True)

    # ------------------------------------------------------------------
    # DX11 setup
    # ------------------------------------------------------------------

    def _setup_dx11(self):
        # 1. D3D11 device (BGRA_SUPPORT required for B8G8R8A8 DComp swap chain)
        self._device, self._context = dx11.create_device()

        # 2. DComp device (shares the GPU)
        dxgi_dev = dx11._query(self._device, dx11._IID_IDXGIDevice)
        self._dcomp_device = dcomp.create_dcomp_device(dxgi_dev)
        dx11._release(dxgi_dev)

        # 3. Flip-model swap chain for DComp (premultiplied alpha, BGRA)
        factory2 = dx11.get_factory2()
        self._swap_chain = dx11.create_swap_chain_for_composition(
            factory2, self._device, self._screen_w, self._screen_h)
        dx11._release(factory2)

        # 4. Wire swap chain into DWM via DComp and commit
        self._dcomp_target = dcomp.create_target(self._dcomp_device, self._hwnd)
        self._dcomp_visual = dcomp.create_visual(self._dcomp_device)
        dcomp.visual_set_content(self._dcomp_visual, self._swap_chain)
        dcomp.target_set_root(self._dcomp_target, self._dcomp_visual)
        dcomp.commit(self._dcomp_device)

        # 5. RTV for rendering into the swap chain back buffer
        self._rtv = dx11.make_rtv(self._device, self._swap_chain)

    # ------------------------------------------------------------------
    # imgui setup
    # ------------------------------------------------------------------

    def _setup_imgui(self):
        imgui.create_context()
        io = imgui.get_io()
        io.set_ini_filename("")                 # no imgui.ini file
        io.set_log_filename("")
        io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard

        self._apply_style()

        self._backend = ImguiBackend(self._hwnd, self._device, self._context)
        self._backend.init()

    # Theme color tables — applied via push_style_color each frame
    _DARK_COLORS: dict = {}
    _LIGHT_COLORS: dict = {}

    @staticmethod
    def _build_color_tables():
        if OverlayApp._DARK_COLORS:
            return
        _bg    = (0.118, 0.118, 0.118, 1.0)
        _bar   = (0.078, 0.078, 0.078, 1.0)
        _btn   = (0.176, 0.176, 0.176, 1.0)
        _hover = (0.227, 0.227, 0.227, 1.0)
        _entry = (0.200, 0.200, 0.200, 1.0)
        _card  = (0.145, 0.145, 0.145, 1.0)
        _bord  = (0.165, 0.165, 0.165, 1.0)
        _acc   = (0.290, 0.620, 1.000, 1.0)
        _text  = (0.800, 0.800, 0.800, 1.0)
        _dim   = (0.533, 0.533, 0.533, 1.0)
        OverlayApp._DARK_COLORS = {
            imgui.Col_.window_bg:           _bg,
            imgui.Col_.popup_bg:            _bg,
            imgui.Col_.child_bg:            _card,
            imgui.Col_.title_bg:            _bar,
            imgui.Col_.title_bg_active:     _bar,
            imgui.Col_.title_bg_collapsed:  _bar,
            imgui.Col_.button:              _btn,
            imgui.Col_.button_hovered:      _hover,
            imgui.Col_.button_active:       _entry,
            imgui.Col_.frame_bg:            _entry,
            imgui.Col_.frame_bg_hovered:    _hover,
            imgui.Col_.frame_bg_active:     _btn,
            imgui.Col_.header:              _btn,
            imgui.Col_.header_hovered:      _hover,
            imgui.Col_.header_active:       _entry,
            imgui.Col_.separator:           _bord,
            imgui.Col_.separator_hovered:   _acc,
            imgui.Col_.separator_active:    _acc,
            imgui.Col_.tab:                 _bar,
            imgui.Col_.tab_hovered:         _btn,
            imgui.Col_.tab_selected:        _bg,
            imgui.Col_.tab_dimmed:          _bar,
            imgui.Col_.tab_dimmed_selected: _bg,
            imgui.Col_.check_mark:          _acc,
            imgui.Col_.slider_grab:         _acc,
            imgui.Col_.slider_grab_active:  _acc,
            imgui.Col_.scrollbar_bg:        _bg,
            imgui.Col_.scrollbar_grab:      _btn,
            imgui.Col_.text:                _text,
            imgui.Col_.text_disabled:       _dim,
            imgui.Col_.border:              _bord,
        }
        _bg    = (0.941, 0.949, 0.957, 1.0)
        _bar   = (0.769, 0.784, 0.800, 1.0)
        _btn   = (0.690, 0.714, 0.737, 1.0)
        _hover = (0.784, 0.800, 0.816, 1.0)
        _entry = (0.867, 0.878, 0.894, 1.0)
        _card  = (0.902, 0.910, 0.918, 1.0)
        _bord  = (0.604, 0.620, 0.635, 1.0)
        _acc   = (0.102, 0.435, 0.831, 1.0)
        _text  = (0.165, 0.165, 0.165, 1.0)
        _dim   = (0.376, 0.376, 0.376, 1.0)
        OverlayApp._LIGHT_COLORS = {
            imgui.Col_.window_bg:           _bg,
            imgui.Col_.popup_bg:            _bg,
            imgui.Col_.child_bg:            _card,
            imgui.Col_.title_bg:            _bar,
            imgui.Col_.title_bg_active:     _bar,
            imgui.Col_.title_bg_collapsed:  _bar,
            imgui.Col_.button:              _btn,
            imgui.Col_.button_hovered:      _hover,
            imgui.Col_.button_active:       _entry,
            imgui.Col_.frame_bg:            _entry,
            imgui.Col_.frame_bg_hovered:    _hover,
            imgui.Col_.frame_bg_active:     _btn,
            imgui.Col_.header:              _btn,
            imgui.Col_.header_hovered:      _hover,
            imgui.Col_.header_active:       _entry,
            imgui.Col_.separator:           _bord,
            imgui.Col_.separator_hovered:   _acc,
            imgui.Col_.separator_active:    _acc,
            imgui.Col_.tab:                 _bar,
            imgui.Col_.tab_hovered:         _btn,
            imgui.Col_.tab_selected:        _bg,
            imgui.Col_.tab_dimmed:          _bar,
            imgui.Col_.tab_dimmed_selected: _bg,
            imgui.Col_.check_mark:          _acc,
            imgui.Col_.slider_grab:         _acc,
            imgui.Col_.slider_grab_active:  _acc,
            imgui.Col_.scrollbar_bg:        _bg,
            imgui.Col_.scrollbar_grab:      _btn,
            imgui.Col_.text:                _text,
            imgui.Col_.text_disabled:       _dim,
            imgui.Col_.border:              _bord,
        }

    def _apply_style(self, dark: bool = True):
        if dark:
            imgui.style_colors_dark()
        else:
            imgui.style_colors_light()

        self._dark_theme = dark
        self._build_color_tables()

        s = imgui.get_style()

        s.window_rounding       = 8.0
        s.child_rounding        = 6.0
        s.frame_rounding        = 4.0
        s.popup_rounding        = 6.0
        s.scrollbar_rounding    = 4.0
        s.grab_rounding         = 4.0
        s.tab_rounding          = 4.0
        s.window_border_size    = 1.0
        s.child_border_size     = 1.0
        s.frame_border_size     = 0.0
        s.item_spacing          = (6.0, 4.0)
        s.item_inner_spacing    = (4.0, 4.0)
        s.frame_padding         = (6.0, 3.0)
        s.window_padding        = (10.0, 8.0)
        s.indent_spacing        = 16.0
        s.scrollbar_size        = 8.0

    # ------------------------------------------------------------------
    # Panel module setup
    # ------------------------------------------------------------------

    def _setup_panels(self):
        from ui_imgui.recoil    import RecoilUI
        from ui_imgui.crosshair import CrosshairUI
        from ui_imgui.remapper  import RemapperUI
        from ui_imgui.macros    import MacrosUI
        from ui_imgui.stats     import StatsUI
        from ui_imgui.profiles  import ProfilesUI
        from ui_imgui.settings  import SettingsUI

        self._panels = {
            _TAB_RECOIL:    RecoilUI(self._settings, self._engine,
                                     self._macro_engine,
                                     self._notify_changed),
            _TAB_CROSSHAIR: CrosshairUI(self._settings, self._engine,
                                        self._notify_changed),
            _TAB_REMAPPER:  RemapperUI(self._settings, self._notify_changed),
            _TAB_MACROS:    MacrosUI(self._settings, self._macro_engine,
                                     self._notify_changed),
            _TAB_STATS:     StatsUI(self._settings, self._notify_changed),
            _TAB_PROFILES:  ProfilesUI(self._profile_data,
                                       self._on_profile_load,
                                       self._on_profile_save,
                                       self._on_profile_delete),
            _TAB_SETTINGS:  SettingsUI(self._settings, self._engine,
                                       self._notify_changed,
                                       self._on_theme_changed,
                                       self._on_capture_suspend,
                                       self.queue.put_quit),
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _main_loop(self):
        msg = wintypes.MSG()
        while self._running:
            # Drain Win32 message queue (non-blocking)
            while _user32.PeekMessageW(byref(msg), None, 0, 0, 1):
                if msg.message == _WM_QUIT:
                    self._running = False
                    break
                _user32.TranslateMessage(byref(msg))
                _user32.DispatchMessageW(byref(msg))

            if not self._running:
                break

            self.queue.drain(self)
            self._render_frame()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_frame(self):
        self._backend.new_frame()
        imgui.new_frame()
        self._backend._update_textures()   # must run after new_frame, before render

        # Push theme colors for this frame
        _colors = self._DARK_COLORS if self._dark_theme else self._LIGHT_COLORS
        for _k, _v in _colors.items():
            imgui.push_style_color(_k, _v)

        # --- Crosshair (behind the panel so panel draws on top) ---
        cs = self._settings.get("crosshair", {})
        if cs.get("enabled") and self._crosshair_should_show():
            self._draw_crosshair()
            self._draw_module_indicators()

        # --- Strength indicator ---
        if self._si_value is not None and time.monotonic() < self._si_until:
            self._draw_strength_indicator()
        elif self._si_value is not None and time.monotonic() >= self._si_until:
            self._si_value = None

        # --- Stats overlay ---
        st = self._settings.get("stats", {})
        if st.get("enabled") and self._stats_data:
            self._draw_stats()

        # --- Panel UI (topbar + content) ---
        if self._panel_visible:
            self._draw_topbar()
            if not self._panel_collapsed:
                self._draw_panel_content()

        # Pop theme colors
        imgui.pop_style_color(len(_colors))

        imgui.render()

        # Submit to DX11
        dx11.ctx_clear_rtv(self._context, self._rtv, 0.0, 0.0, 0.0, 0.0)
        dx11.ctx_set_rtv(self._context, self._rtv)
        dx11.ctx_set_viewport(self._context, float(self._screen_w), float(self._screen_h))
        self._backend.render(imgui.get_draw_data(), self._rtv)
        dx11.swap_present(self._swap_chain, 0, 0)   # no vsync — game controls timing

    # ------------------------------------------------------------------
    # Topbar
    # ------------------------------------------------------------------

    _ACCENT_COL    = _col32(74,  158, 255, 255)   # #4a9eff
    _DIM_COL       = _col32(136, 136, 136, 255)   # #888888
    _ACTIVE_FG_COL = _col32(34,  197, 94,  255)   # #22c55e
    _RED_COL       = _col32(255, 102, 102, 255)   # #ff6666

    _LEFT_TABS  = [("WEAPON",    _TAB_RECOIL),
                   ("CROSSHAIR", _TAB_CROSSHAIR),
                   ("REMAPPER",  _TAB_REMAPPER),
                   ("MACROS",    _TAB_MACROS),
                   ("STATS",     _TAB_STATS)]
    _RIGHT_TABS = [("PROFILES",  _TAB_PROFILES),
                   ("SETTINGS",  _TAB_SETTINGS)]

    def _draw_topbar(self):
        w, h = float(self._screen_w), float(_TOPBAR_TOTAL)

        imgui.set_next_window_pos((_TOPBAR_MARGIN_SIDE, _TOPBAR_MARGIN_TOP))
        imgui.set_next_window_size((w - 2 * _TOPBAR_MARGIN_SIDE, _TOPBAR_H))
        imgui.set_next_window_bg_alpha(1.0)

        flags = (imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                 | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_scroll_with_mouse
                 | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_scrollbar)

        # Flash border color
        _flashing = time.monotonic() < self._flash_until
        if _flashing:
            _s = imgui.get_style()
            _prev_border = _s.color_(imgui.Col_.border)
            _prev_bsize  = _s.window_border_size
            _s.color_(imgui.Col_.border, _int_to_rgba(self._flash_col))
            _s.window_border_size = 2.0

        imgui.begin("##topbar", flags=flags)

        # Left tabs
        for label, tab_idx in self._LEFT_TABS:
            active = (tab_idx == self._active_tab and self._panel_visible)
            if active:
                imgui.push_style_color(imgui.Col_.text, _int_to_rgba(self._ACCENT_COL))
            else:
                imgui.push_style_color(imgui.Col_.text, _int_to_rgba(self._DIM_COL))
            imgui.push_style_color(imgui.Col_.button,         (0, 0, 0, 0))
            imgui.push_style_color(imgui.Col_.button_hovered, (0, 0, 0, 0.15))
            imgui.push_style_color(imgui.Col_.button_active,  (0, 0, 0, 0.1))
            if imgui.button(label + "##t"):
                self._toggle_tab(tab_idx)
            imgui.pop_style_color(4)
            imgui.same_line()

        # Spacer
        avail = imgui.get_content_region_avail().x
        right_size = sum(
            imgui.calc_text_size(lbl).x + 16 for lbl, _ in self._RIGHT_TABS
        ) + 38  # quit button
        spacer = avail - right_size
        if spacer > 0:
            imgui.dummy((spacer, 1))
            imgui.same_line()

        # Right tabs
        for label, tab_idx in self._RIGHT_TABS:
            active = (tab_idx == self._active_tab and self._panel_visible)
            if active:
                imgui.push_style_color(imgui.Col_.text, _int_to_rgba(self._ACCENT_COL))
            else:
                imgui.push_style_color(imgui.Col_.text, _int_to_rgba(self._DIM_COL))
            imgui.push_style_color(imgui.Col_.button,         (0, 0, 0, 0))
            imgui.push_style_color(imgui.Col_.button_hovered, (0, 0, 0, 0.15))
            imgui.push_style_color(imgui.Col_.button_active,  (0, 0, 0, 0.1))
            if imgui.button(label + "##t"):
                self._toggle_tab(tab_idx)
            imgui.pop_style_color(4)
            imgui.same_line()

        # Quit button
        imgui.push_style_color(imgui.Col_.text, _int_to_rgba(self._RED_COL))
        imgui.push_style_color(imgui.Col_.button,         (0, 0, 0, 0))
        imgui.push_style_color(imgui.Col_.button_hovered, (0, 0, 0, 0.15))
        imgui.push_style_color(imgui.Col_.button_active,  (0, 0, 0, 0.1))
        if imgui.button("X##quit"):
            self._running = False
        imgui.pop_style_color(4)

        imgui.end()

        # Restore border color if flashing
        if _flashing:
            _s = imgui.get_style()
            _s.color_(imgui.Col_.border, _prev_border)
            _s.window_border_size = _prev_bsize

    # ------------------------------------------------------------------
    # Panel content
    # ------------------------------------------------------------------

    def _draw_panel_content(self):
        right = self._active_tab in _RIGHT_ANCHOR_TABS
        px = (self._screen_w - _TOPBAR_MARGIN_SIDE - _PANEL_W
              if right else _TOPBAR_MARGIN_SIDE)
        py = float(_TOPBAR_TOTAL)

        imgui.set_next_window_pos((px, py))
        imgui.set_next_window_size((_PANEL_W, 0.0))   # auto height
        imgui.set_next_window_bg_alpha(1.0)

        flags = (imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                 | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_saved_settings)

        imgui.begin("##panel", flags=flags)

        panel = self._panels.get(self._active_tab)
        if panel:
            panel.draw()

        new_h = imgui.get_window_height()
        if new_h != self._panel_h:
            self._panel_h = new_h
            self._update_hit_region()
        imgui.end()

    # ------------------------------------------------------------------
    # Crosshair
    # ------------------------------------------------------------------

    def _crosshair_should_show(self) -> bool:
        wf  = self._settings.get("window_filter", "")
        return True if not wf else self._engine.windowMatchesFilter(wf)

    def _draw_crosshair(self):
        cs     = self._settings.get("crosshair", {})
        style  = cs.get("style",        "cross")
        cname  = cs.get("color",        "green")
        size   = cs.get("size",         10)
        thick  = float(cs.get("thickness",    2))
        gap    = cs.get("gap",          4)
        outline = cs.get("outline_size", 1)

        fg  = _ch_col32(cname)
        blk = _col32(0, 0, 0)
        cx  = self._screen_w / 2
        cy  = self._screen_h / 2
        dl  = imgui.get_foreground_draw_list()

        has_cross  = style in ("cross",  "dot_cross")
        has_dot    = style in ("dot",    "dot_cross", "circle_dot")
        has_circle = style in ("circle", "circle_dot")

        ot = thick + outline * 2

        if has_cross and outline > 0:
            dl.add_line((cx - gap - size, cy), (cx - gap, cy), blk, ot)
            dl.add_line((cx + gap, cy), (cx + gap + size, cy), blk, ot)
            dl.add_line((cx, cy - gap - size), (cx, cy - gap), blk, ot)
            dl.add_line((cx, cy + gap), (cx, cy + gap + size), blk, ot)
        if has_cross:
            dl.add_line((cx - gap - size, cy), (cx - gap, cy), fg, thick)
            dl.add_line((cx + gap, cy), (cx + gap + size, cy), fg, thick)
            dl.add_line((cx, cy - gap - size), (cx, cy - gap), fg, thick)
            dl.add_line((cx, cy + gap), (cx, cy + gap + size), fg, thick)

        if has_dot and outline > 0:
            dl.add_circle_filled((cx, cy), thick + outline, blk)
        if has_dot:
            dl.add_circle_filled((cx, cy), thick, fg)

        if has_circle and outline > 0:
            dl.add_circle((cx, cy), float(size), blk, 0, ot)
        if has_circle:
            dl.add_circle((cx, cy), float(size), fg, 0, thick)

    def _draw_module_indicators(self):
        cs     = self._settings.get("crosshair", {})
        cname  = cs.get("color", "green")
        fg     = _ch_col32(cname)
        blk    = _col32(0, 0, 0)
        dl     = imgui.get_foreground_draw_list()

        labels = []
        if self._settings.get("recoil",    {}).get("enabled"): labels.append("R")
        if self._settings.get("rapidfire", {}).get("enabled"): labels.append("RF")
        if not labels:
            return

        cx = self._screen_w / 2
        cy = self._screen_h / 2 + 30
        # Use imgui foreground draw list text rendering
        font_size = imgui.get_font_size()
        spacing   = 6.0
        widths    = [imgui.calc_text_size(lbl).x for lbl in labels]
        total_w   = sum(widths) + spacing * (len(labels) - 1)
        x         = cx - total_w / 2

        for i, lbl in enumerate(labels):
            for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
                dl.add_text((x + dx, cy + dy), blk, lbl)
            dl.add_text((x, cy), fg, lbl)
            x += widths[i] + spacing

    # ------------------------------------------------------------------
    # Strength indicator
    # ------------------------------------------------------------------

    def _draw_strength_indicator(self):
        if self._si_value is None:
            return
        text = str(self._si_value)
        cx   = self._screen_w / 2 - 10
        cy   = self._screen_h / 2 - 24
        dl   = imgui.get_foreground_draw_list()
        blk  = _col32(0, 0, 0)
        wht  = _col32(255, 255, 255)
        for dx, dy in ((-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)):
            dl.add_text((cx + dx, cy + dy), blk, text)
        dl.add_text((cx, cy), wht, text)

    # ------------------------------------------------------------------
    # Stats overlay
    # ------------------------------------------------------------------

    def _draw_stats(self):
        lines = self._stats_visible_lines()
        if not lines:
            return

        st     = self._settings.get("stats", {})
        corner = st.get("corner",     "top_right")
        alpha  = st.get("bg_alpha",   70) / 100.0
        tcol   = _hex_col32(st.get("text_color", "#ffffff"))
        dim    = _shade_col32(int(221 - 85 * alpha))

        margin = 12
        w      = float(_PANEL_W)
        row_h  = 18.0
        pad    = 6.0
        h      = pad * 2 + len(lines) * row_h

        # Position
        scr_w, scr_h = float(self._screen_w), float(self._screen_h)
        top_offset   = float(_TOPBAR_TOTAL) + margin
        if "left" in corner:
            px = margin
        else:
            px = scr_w - w - margin
        if "top" in corner:
            py = top_offset
        elif "bottom" in corner:
            py = scr_h - h - margin
        else:
            py = (scr_h - h) / 2

        shade = int(136 * (1.0 - alpha))
        bg    = _shade_col32(shade, 220)

        imgui.set_next_window_pos((px, py), imgui.Cond_.always)
        imgui.set_next_window_size((w, 0.0))
        imgui.set_next_window_bg_alpha(1.0 - alpha + 0.01)  # ensure not fully transparent

        flags = (imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.no_resize
                 | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_saved_settings
                 | imgui.WindowFlags_.no_nav_inputs | imgui.WindowFlags_.no_inputs
                 | imgui.WindowFlags_.no_nav_focus)

        # Custom background via draw list (before Begin so it goes behind)
        dl = imgui.get_foreground_draw_list()
        dl.add_rect_filled((px, py), (px + w, py + h), bg, 8.0)

        imgui.begin("##stats", flags=flags)
        for label, value in lines:
            imgui.text_colored(_int_to_rgba(dim), label)
            imgui.same_line(0, 8)
            imgui.text_colored(_int_to_rgba(tcol), value)
        imgui.end()

    def _stats_visible_lines(self) -> list[tuple[str, str]]:
        st = self._settings.get("stats", {})
        d  = self._stats_data
        out = []
        if st.get("show_cpu_usage") and "cpu_usage" in d:
            val = f"{d['cpu_usage']:.0f}%"
            if st.get("show_cpu_temp") and "cpu_temp" in d:
                val += f"  {d['cpu_temp']:.0f}\u00b0C"
            out.append(("CPU", val))
        elif st.get("show_cpu_temp") and "cpu_temp" in d:
            out.append(("CPU TEMP", f"{d['cpu_temp']:.0f}\u00b0C"))
        if st.get("show_gpu_usage") and "gpu_usage" in d:
            val = f"{d['gpu_usage']:.0f}%"
            if st.get("show_gpu_temp") and "gpu_temp" in d:
                val += f"  {d['gpu_temp']:.0f}\u00b0C"
            out.append(("GPU", val))
        elif st.get("show_gpu_temp") and "gpu_temp" in d:
            out.append(("GPU TEMP", f"{d['gpu_temp']:.0f}\u00b0C"))
        if st.get("show_gpu_vram") and "gpu_vram_used" in d:
            if "gpu_vram_total" in d:
                out.append(("VRAM", f"{d['gpu_vram_used']:.1f}/{d['gpu_vram_total']:.1f} GB"))
            else:
                out.append(("VRAM", f"{d['gpu_vram_used']:.1f} GB"))
        if st.get("show_ram") and "ram_used" in d:
            if "ram_total" in d:
                out.append(("RAM", f"{d['ram_used']:.1f}/{d['ram_total']:.1f} GB"))
            else:
                out.append(("RAM", f"{d['ram_used']:.1f} GB"))
        return out

    # ------------------------------------------------------------------
    # Win32 WndProc
    # ------------------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        # WM_NCHITTEST must be handled first — determines click-through before anything else
        if msg == _WM_NCHITTEST:
            if not self._panel_visible:
                return _HTTRANSPARENT
            # Decode cursor position (screen coords)
            mx = ctypes.c_short(lparam & 0xFFFF).value
            my = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            # Topbar area
            if (0 <= mx <= self._screen_w and
                    _TOPBAR_MARGIN_TOP <= my <= _TOPBAR_TOTAL):
                return _HTCLIENT
            # Panel area
            right = self._active_tab in _RIGHT_ANCHOR_TABS
            px = (self._screen_w - _TOPBAR_MARGIN_SIDE - _PANEL_W
                  if right else _TOPBAR_MARGIN_SIDE)
            py = _TOPBAR_TOTAL
            if (px <= mx <= px + _PANEL_W and
                    py <= my <= py + self._panel_h + 20):
                return _HTCLIENT
            return _HTTRANSPARENT

        # Forward to imgui backend
        if self._backend and self._backend.process_win32_message(msg, wparam, lparam):
            return _DefWindowProcW(hwnd, msg, wparam, lparam)

        if msg == _WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0

        if msg == _WM_CLOSE:
            self._running = False
            return 0

        if msg == _WM_SIZE:
            if wparam != 0:   # not minimized
                w = lparam & 0xFFFF
                h = (lparam >> 16) & 0xFFFF
                if w > 0 and h > 0 and (w != self._screen_w or h != self._screen_h):
                    self._screen_w, self._screen_h = w, h
                    if self._rtv:
                        dx11._release(self._rtv)
                        self._rtv = 0
                    dx11.swap_resize(self._swap_chain, w, h)
                    self._rtv = dx11.make_rtv(self._device, self._swap_chain)
            return 0

        return _DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------

    def _update_hit_region(self):
        """Set the window region to only the interactive UI areas.

        Pixels outside the region get no mouse events — they fall through to the game.
        passthrough (panel hidden): 0×0 region → nothing hittable.
        panel visible: topbar rect ∪ panel rect → only those areas receive mouse input.
        """
        if not self._panel_visible:
            rgn = _gdi32.CreateRectRgn(0, 0, 0, 0)
            _user32.SetWindowRgn(self._hwnd, rgn, False)
            return

        # Topbar region
        bar_rgn = _gdi32.CreateRectRgn(
            _TOPBAR_MARGIN_SIDE, _TOPBAR_MARGIN_TOP,
            self._screen_w - _TOPBAR_MARGIN_SIDE, _TOPBAR_TOTAL)

        # Panel region (only when not collapsed)
        if not self._panel_collapsed:
            right = self._active_tab in _RIGHT_ANCHOR_TABS
            px = (self._screen_w - _TOPBAR_MARGIN_SIDE - _PANEL_W
                  if right else _TOPBAR_MARGIN_SIDE)
            ph = max(int(self._panel_h) + 20, 200)
            pan_rgn = _gdi32.CreateRectRgn(px, _TOPBAR_TOTAL, px + _PANEL_W, _TOPBAR_TOTAL + ph)
            _gdi32.CombineRgn(bar_rgn, bar_rgn, pan_rgn, _RGN_OR)
            _gdi32.DeleteObject(pan_rgn)

        # System takes ownership of bar_rgn — do NOT DeleteObject it
        _user32.SetWindowRgn(self._hwnd, bar_rgn, True)

    def _set_click_through(self, passthrough: bool):
        """Called when panel visibility changes."""
        self._update_hit_region()

    def _toggle_overlay(self):
        if self._panel_visible:
            self._profile_data["last_tab"] = self._active_tab
            prof.save(self._profile_data)
            self._panel_visible   = False
            self._panel_collapsed = False
            self._set_click_through(True)
        else:
            self._panel_visible   = True
            self._panel_collapsed = False
            self._set_click_through(False)

    def _toggle_tab(self, idx: int):
        if idx == self._active_tab:
            if self._panel_collapsed:
                self._panel_collapsed = False
                self._panel_visible   = True
            else:
                self._panel_collapsed = True
        else:
            self._active_tab      = idx
            self._panel_collapsed = False
            self._panel_visible   = True

    def _notify_changed(self, updated: dict | None = None):
        self._on_settings_changed(self._settings)

    def _on_theme_changed(self, name: str):
        self._settings["theme"] = name
        self._apply_style(dark=(name == "Dark"))
        self._notify_changed()

    def _on_capture_suspend(self, suspending: bool):
        self._engine.setSuspendHotkeys(suspending)

    # ------------------------------------------------------------------
    # Profile operations
    # ------------------------------------------------------------------

    def _on_profile_load(self, name: str):
        settings = prof.loadProfile(self._profile_data, name)
        if settings is None:
            return
        old_theme = self._settings.get("theme", "Dark")
        self._engine.updateSettings(settings)
        for key in settings:
            self._settings[key] = settings[key]
        if self._settings.get("theme", "Dark") != old_theme:
            self._apply_style(dark=(self._settings["theme"] == "Dark"))
        for panel in self._panels.values():
            if hasattr(panel, "reload"):
                panel.reload(self._settings)
        self._panels[_TAB_PROFILES].refresh_combo()
        self._flash(_col32(68, 255, 136), 0.4)       # green — load

    def _on_profile_save(self, name: str):
        if prof.saveProfile(self._profile_data, name, self._settings):
            self._panels[_TAB_PROFILES].refresh_combo()
            self._flash(_col32(68, 136, 255), 0.4)   # blue — save

    def _on_profile_delete(self, name: str):
        was_active = (name == self._profile_data.get("active"))
        if not prof.deleteProfile(self._profile_data, name):
            return
        self._panels[_TAB_PROFILES].refresh_combo()
        self._flash(_col32(255, 68, 68), 0.4)        # red — delete
        if was_active:
            settings = prof.loadProfile(self._profile_data, prof.DEFAULT_NAME)
            if settings:
                old_theme = self._settings.get("theme", "Dark")
                self._engine.updateSettings(settings)
                for key in settings:
                    self._settings[key] = settings[key]
                if self._settings.get("theme", "Dark") != old_theme:
                    self._apply_style(dark=(self._settings["theme"] == "Dark"))
                for panel in self._panels.values():
                    if hasattr(panel, "reload"):
                        panel.reload(self._settings)

    def _flash(self, col: int, duration: float):
        self._flash_col   = col
        self._flash_until = time.monotonic() + duration

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup(self):
        if self._backend:
            self._backend.shutdown()
            self._backend = None
        imgui.destroy_context()
        for attr in ("_rtv", "_swap_chain", "_context", "_device"):
            v = getattr(self, attr, 0)
            if v:
                dx11._release(v)
                setattr(self, attr, 0)
        for attr in ("_dcomp_visual", "_dcomp_target", "_dcomp_device"):
            v = getattr(self, attr, 0)
            if v:
                dx11._release(v)
                setattr(self, attr, 0)
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = 0


# ---------------------------------------------------------------------------
# Color conversion utility
# ---------------------------------------------------------------------------

def _int_to_rgba(col: int) -> tuple[float, float, float, float]:
    """Convert packed IM_COL32 int to (r,g,b,a) float tuple for imgui style."""
    r = (col >> 0)  & 0xFF
    g = (col >> 8)  & 0xFF
    b = (col >> 16) & 0xFF
    a = (col >> 24) & 0xFF
    return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
