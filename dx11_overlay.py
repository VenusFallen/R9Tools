"""
dx11_overlay.py — DX11 crosshair + stats overlay.

Replaces overlay_window.py + stats_overlay.py.

Transparency approach: DirectComposition (dcomp.dll).
  - Window style: WS_EX_NOREDIRECTIONBITMAP (no GDI surface, no DWM blt overhead)
  - Swap chain: CreateSwapChainForComposition, FLIP_DISCARD, BGRA premultiplied alpha
  - Transparent pixels are (0,0,0,0); clear the RTV to (0,0,0,0) each frame
  - DWM can assign the overlay to a dedicated hardware plane (MPO), leaving the
    game's swap chain free to use Independent Flip → zero composition overhead

Architecture:
  - Runs in a background thread; call start() then stop() from the main thread.
  - main thread → overlay: thread-safe setters (all guarded by GIL / simple assign)
  - StatsPoller → overlay: update_stats(dict) protected by threading.Lock
  - Always click-through: WS_EX_LAYERED | WS_EX_TRANSPARENT makes DWM resolve
    hit-testing automatically — no custom WM_NCHITTEST handling is needed
    (see the ex_style comment in _create_window for why WS_EX_LAYERED is
    load-bearing here).
"""
import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
import time

import dx11_bridge as dx
import dcomp_bridge as dcomp
from dx11_renderer import Renderer

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_WS_POPUP                  = 0x80000000
_WS_EX_TOPMOST             = 0x00000008
_WS_EX_NOACTIVATE          = 0x08000000
_WS_EX_TOOLWINDOW          = 0x00000080
_WS_EX_TRANSPARENT         = 0x00000020
_WS_EX_LAYERED             = 0x00080000
_WS_EX_NOREDIRECTIONBITMAP = 0x00200000  # no GDI surface — DComp owns the content

_WM_DESTROY        = 0x0002
_WM_SIZE           = 0x0005
_WM_DISPLAYCHANGE  = 0x007E

_PM_REMOVE         = 0x0001

_SM_CXSCREEN       = 0
_SM_CYSCREEN       = 1

_GWL_EXSTYLE       = -20

_HWND_TOPMOST      = -1
_SWP_NOMOVE        = 0x0002
_SWP_NOSIZE        = 0x0001
_SWP_NOACTIVATE    = 0x0010

# GetWindowLongPtrW / SetWindowLongPtrW are the 64-bit-safe variants; on 32-bit
# processes ctypes falls back automatically since user32 exports both symbols
# only on 64-bit builds, so guard with getattr.
_GetWindowLongPtrW = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
_GetWindowLongPtrW.restype  = ctypes.c_int64 if hasattr(_user32, "GetWindowLongPtrW") else ctypes.c_int32
_GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

_SetWindowPos          = _user32.SetWindowPos
_SetWindowPos.restype  = wintypes.BOOL
_SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]

# ---------------------------------------------------------------------------
# 64-bit-safe WNDPROC types and DefWindowProcW
# On x64 Windows WPARAM/LPARAM/LRESULT are all 64-bit; ctypes.wintypes defines
# them as 32-bit (c_ulong / c_long) which causes OverflowError for large lparams.
# ---------------------------------------------------------------------------

_WPARAM  = ctypes.c_uint64
_LPARAM  = ctypes.c_int64
_LRESULT = ctypes.c_int64

_DefWindowProcW = _user32.DefWindowProcW
_DefWindowProcW.restype  = _LRESULT
_DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, _WPARAM, _LPARAM]

# ---------------------------------------------------------------------------
# Crosshair colour map
# ---------------------------------------------------------------------------

_COLOR_MAP = {
    "green":  (0.0,  1.0,  0.0,  1.0),
    "red":    (1.0,  0.0,  0.0,  1.0),
    "white":  (1.0,  1.0,  1.0,  1.0),
    "pink":   (1.0,  0.08, 0.576, 1.0),
    "yellow": (1.0,  1.0,  0.0,  1.0),
}
_BLACK = (0.0, 0.0, 0.0, 1.0)
_WHITE = (1.0, 1.0, 1.0, 1.0)


def _hex_to_col(hex_str: str) -> tuple:
    h = hex_str.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return r / 255.0, g / 255.0, b / 255.0, 1.0
    return (1.0, 1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Win32 window proc
# ---------------------------------------------------------------------------

_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND, wintypes.UINT, _WPARAM, _LPARAM
)


class _WndClass(ctypes.Structure):
    _fields_ = [
        ("style",         wintypes.UINT),
        ("lpfnWndProc",   _WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     wintypes.HINSTANCE),
        ("hIcon",         wintypes.HICON),
        ("hCursor",       wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName",  wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd",    wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam",  wintypes.WPARAM),
        ("lParam",  wintypes.LPARAM),
        ("time",    wintypes.DWORD),
        ("pt",      wintypes.POINT),
    ]


# ---------------------------------------------------------------------------
# DX11Overlay
# ---------------------------------------------------------------------------

class DX11Overlay:
    """
    Full-screen transparent DX11 overlay.  Runs in its own thread.

    Public thread-safe methods (callable from any thread):
      start()
      stop()
      refresh()             — re-evaluate crosshair visibility
      update_stats(data)    — update the stats HUD
      show_strength_indicator(value)  — flash SI for 500ms
    """

    _CLASS_NAME = "R9DX11Overlay"
    _POLL_INTERVAL = 0.5   # seconds between window-filter polls

    def __init__(self, settings: dict, engine):
        self._settings   = settings
        self._engine     = engine

        self._ch_visible  = False
        self._ind_visible = False
        self._si_value   = None
        self._si_until   = 0.0

        # Driven by set_input_failed() below; simple bool assign, read every
        # frame by this (separate) render thread, same GIL-guarded
        # convention as _ch_visible/_ind_visible above.
        self._input_failed = False

        self._stats_lock = threading.Lock()
        self._stats_data: dict = {}

        self._running    = False
        self._hwnd       = 0
        self._thread     = None
        self._ready      = threading.Event()   # set when window+DX are initialised

        # DX11 + DComp objects (owned by render thread)
        self._device       = 0
        self._context      = 0
        self._swap_chain   = 0
        self._rtv          = 0
        self._renderer: Renderer | None = None
        self._dcomp_device = 0
        self._dcomp_target = 0
        self._dcomp_visual = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True,
                                          name="DX11Overlay")
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self):
        self._running = False
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, _WM_DESTROY, 0, 0)
        if self._thread:
            self._thread.join(timeout=3.0)

    def refresh(self):
        """Re-evaluate crosshair + module-indicator visibility (settings + window filter)."""
        wf = self._settings.get("window_filter", "")

        cs = self._settings.get("crosshair", {})
        if not cs.get("enabled", False):
            self._ch_visible = False
        else:
            self._ch_visible = True if not wf else self._engine.windowMatchesFilter(wf)

        ind = self._settings.get("indicator", {})
        if not ind.get("enabled", True):
            self._ind_visible = False
        else:
            self._ind_visible = True if not wf else self._engine.windowMatchesFilter(wf)

    def update_stats(self, data: dict):
        with self._stats_lock:
            self._stats_data = data

    def show_strength_indicator(self, value: int):
        self._si_value = value
        self._si_until = time.monotonic() + 0.5

    def set_input_failed(self, failed: bool = True):
        """Turns the always-on running-indicator badge red and adds an
        'Input Failed' label underneath it (failed=True), or reverts it to
        the normal badge (failed=False). main.py combines two sources
        (the sticky I/O-exception path and the recoverable WM_DEVICECHANGE
        path) into the single bool passed here."""
        self._input_failed = failed

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self):
        try:
            self._create_window()
            self._create_dx11()
            self._ready.set()
            self._render_loop()
        except Exception as exc:
            print(f"[DX11Overlay] Fatal: {exc}")
            logging.exception("[DX11Overlay] Fatal error in render thread")
            self._ready.set()
        finally:
            self._teardown()

    # ------------------------------------------------------------------
    # Window creation
    # ------------------------------------------------------------------

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == _WM_DESTROY:
            self._running = False
            _user32.PostQuitMessage(0)
            return 0
        if msg == _WM_SIZE:
            if self._swap_chain and self._renderer:
                w = lparam & 0xFFFF
                h = (lparam >> 16) & 0xFFFF
                if w > 0 and h > 0:
                    self._on_resize(w, h)
            return 0
        if msg == _WM_DISPLAYCHANGE:
            # Without this, self._sw/_sh (and every centre-point calc that
            # reads them) would stay pinned to the stale startup resolution
            # after a live resolution change until the app restarts.
            self._handle_display_change()
            return 0
        return _DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_window(self):
        hinstance = _kernel32.GetModuleHandleW(None)
        self._wnd_proc_cb = _WNDPROC(self._wnd_proc)

        wc = _WndClass()
        wc.style         = 0
        wc.lpfnWndProc   = self._wnd_proc_cb
        wc.hInstance     = hinstance
        wc.lpszClassName = self._CLASS_NAME

        _user32.RegisterClassW(ctypes.byref(wc))

        sw = _user32.GetSystemMetrics(_SM_CXSCREEN)
        sh = _user32.GetSystemMetrics(_SM_CYSCREEN)
        self._sw = sw
        self._sh = sh

        # WS_EX_NOREDIRECTIONBITMAP: no GDI-accessible surface for this window;
        # DComp owns the visual content and DWM reads the DXGI swap chain directly.
        # WS_EX_LAYERED is required for real click-through here even though
        # SetLayeredWindowAttributes/UpdateLayeredWindow are never called —
        # WS_EX_TRANSPARENT + WM_NCHITTEST alone let clicks get swallowed on this
        # window type. Don't remove it without re-verifying click-through with a
        # live SendInput test, since WM_NCHITTEST diagnostics alone can look
        # correct while clicks still don't pass through.
        ex_style = (_WS_EX_TOPMOST | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
                    | _WS_EX_TRANSPARENT | _WS_EX_NOREDIRECTIONBITMAP
                    | _WS_EX_LAYERED)

        hwnd = _user32.CreateWindowExW(
            ex_style,
            self._CLASS_NAME, "R9Overlay",
            _WS_POPUP,
            0, 0, sw, sh,
            None, None, hinstance, None,
        )
        if not hwnd:
            _user32.UnregisterClassW(self._CLASS_NAME, hinstance)
            raise OSError(f"CreateWindowEx failed: {ctypes.GetLastError()}")
        self._hwnd = hwnd

        # Show immediately — with DComp/premultiplied-alpha the window is visually
        # transparent until we present a non-zero frame.  No show/hide toggling needed.
        _user32.ShowWindow(hwnd, 1)   # SW_SHOWNORMAL

        # Defensive: WS_EX_TOPMOST passed to CreateWindowExW is *usually* honoured
        # for initial z-order placement, but this is not universally reliable
        # (some games repeatedly reassert their own HWND_TOPMOST). Explicitly
        # (re-)insert ourselves at the top of the topmost band right after
        # creation so we don't silently start out (or drift) behind the game.
        _SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                      _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)

        # Verify — don't trust — that the extended style we asked for was
        # actually retained by the OS on this live HWND. Printed once at
        # startup so it's visible in the console during a live-testing session.
        exstyle = _GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
        print(f"[DX11Overlay] hwnd=0x{hwnd:X} exstyle=0x{exstyle & 0xFFFFFFFF:08X} "
              f"TRANSPARENT={'yes' if exstyle & _WS_EX_TRANSPARENT else 'NO'} "
              f"TOPMOST={'yes' if exstyle & _WS_EX_TOPMOST else 'NO'} "
              f"LAYERED={'yes (expected — required for real click-through, see comment above)' if exstyle & _WS_EX_LAYERED else 'NO (click-through will likely be broken)'} "
              f"NOREDIRECTIONBITMAP={'yes' if exstyle & _WS_EX_NOREDIRECTIONBITMAP else 'NO'}")

    # ------------------------------------------------------------------
    # DX11 + DirectComposition setup
    # ------------------------------------------------------------------

    def _create_dx11(self):
        # 1. D3D11 device (BGRA_SUPPORT flag required for B8G8R8A8 swap chain)
        self._device, self._context = dx.create_device()

        # 2. DComp device shares our GPU device
        dxgi_dev = dx._query(self._device, dx._IID_IDXGIDevice)
        self._dcomp_device = dcomp.create_dcomp_device(dxgi_dev)
        dx._release(dxgi_dev)

        # 3. Flip-model swap chain registered with DComp
        factory2 = dx.get_factory2()
        self._swap_chain = dx.create_swap_chain_for_composition(
            factory2, self._device, self._sw, self._sh)
        dx._release(factory2)

        # 4. Wire the swap chain into DWM's composition tree and commit
        self._dcomp_target = dcomp.create_target(self._dcomp_device, self._hwnd)
        self._dcomp_visual = dcomp.create_visual(self._dcomp_device)
        dcomp.visual_set_content(self._dcomp_visual, self._swap_chain)
        dcomp.target_set_root(self._dcomp_target, self._dcomp_visual)
        dcomp.commit(self._dcomp_device)

        # 5. RTV and renderer
        self._rtv      = dx.make_rtv(self._device, self._swap_chain)
        self._renderer = Renderer(self._device, self._context, self._sw, self._sh)
        dx.ctx_set_viewport(self._context, float(self._sw), float(self._sh))
        dx.ctx_set_rtv(self._context, self._rtv)

    # ------------------------------------------------------------------
    # Render loop
    # ------------------------------------------------------------------

    def _render_loop(self):
        msg           = _MSG()
        last_poll     = 0.0
        last_diag     = 0.0
        _DIAG_INTERVAL = 2.0   # seconds — separate, coarser cadence than the
                               # filter poll, just to keep console spam down.
        FRAME         = 1.0 / 60.0
        _had_content  = False   # True when last frame had visible content

        # This window is WS_EX_TOPMOST + WS_EX_TRANSPARENT + LAYERED and is
        # the click-through gate for the whole screen; DWM resolves
        # click-through automatically (see _create_window's ex_style
        # comment). Messages are pumped every frame regardless, so
        # WM_SIZE/WM_DESTROY are serviced promptly rather than delayed up
        # to POLL_INTERVAL.
        while self._running:
            t0 = time.monotonic()

            # Process Win32 messages (non-blocking) — always, every tick.
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
                if msg.message == 0x0012:   # WM_QUIT
                    self._running = False
                    break

            if not self._running:
                break

            # Window filter poll every POLL_INTERVAL (independent of message pump cadence)
            if t0 - last_poll >= self._POLL_INTERVAL:
                self.refresh()
                last_poll = t0

                # Defensive re-assert: if some other topmost window (a game
                # forcing itself HWND_TOPMOST) has knocked us out of the
                # topmost band since the last poll, put ourselves back on top.
                _SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                              _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)

            # Diagnostics — periodic sanity check that the ex-style bits this
            # window depends on for click-through and z-order haven't been
            # stripped by the OS/DWM at runtime. Not sufficient alone to
            # diagnose click-through regressions — use a live SendInput +
            # GetForegroundWindow test instead.
            if t0 - last_diag >= _DIAG_INTERVAL:
                exstyle = _GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
                print(f"[DX11Overlay] exstyle sanity check: "
                      f"TRANSPARENT={'yes' if exstyle & _WS_EX_TRANSPARENT else 'NO'} "
                      f"LAYERED={'yes' if exstyle & _WS_EX_LAYERED else 'NO'} "
                      f"TOPMOST={'yes' if exstyle & _WS_EX_TOPMOST else 'NO'}")
                last_diag = t0

            # Determine if anything needs to be drawn
            now          = time.monotonic()
            si_active    = self._si_value is not None and now < self._si_until
            stats_active = self._settings.get("stats", {}).get("enabled", False)
            ri_active    = self._settings.get("running_indicator", {}).get("enabled", True)
            needs_draw   = (self._ch_visible or self._ind_visible
                            or si_active or stats_active or ri_active)

            if needs_draw:
                _had_content = True
                self._render_frame()
            elif _had_content:
                # Present one fully transparent frame to clear the overlay.
                # After this the DComp visual shows (0,0,0,0) — invisible.
                dx.ctx_clear_rtv(self._context, self._rtv, 0.0, 0.0, 0.0, 0.0)
                dx.swap_present(self._swap_chain, 0, 0)
                _had_content = False

            # Always tick at ~frame rate so PeekMessageW is serviced promptly —
            # never block the message queue for a long stretch (see note above).
            elapsed = time.monotonic() - t0
            sleep   = FRAME - elapsed
            if sleep > 0.001:
                time.sleep(sleep)

    def _render_frame(self):
        ctx = self._context
        r   = self._renderer

        # Clear to fully transparent — DComp premultiplied-alpha (0,0,0,0) = invisible.
        dx.ctx_clear_rtv(ctx, self._rtv, 0.0, 0.0, 0.0, 0.0)
        dx.ctx_set_rtv(ctx, self._rtv)

        r.begin()

        if self._ch_visible:
            self._draw_crosshair(r)
        if self._ind_visible:
            self._draw_module_indicators(r)

        # Stats HUD
        st = self._settings.get("stats", {})
        if st.get("enabled", False):
            with self._stats_lock:
                data = dict(self._stats_data)
            self._draw_stats(r, st, data)

        # "R9Tools is loaded" running indicator — always drawn when enabled,
        # independent of crosshair, module indicators, and the window filter
        # (unlike those, this badge is meant to confirm the app is alive
        # regardless of which window currently has focus).
        if self._settings.get("running_indicator", {}).get("enabled", True):
            self._draw_running_indicator(r)

        # Strength indicator
        now = time.monotonic()
        if self._si_value is not None and now < self._si_until:
            self._draw_si(r)
        elif self._si_until and now >= self._si_until:
            self._si_value = None

        r.end()
        dx.swap_present(self._swap_chain, 0, 0)   # no vsync → game controls timing

    # ------------------------------------------------------------------
    # Crosshair drawing
    # ------------------------------------------------------------------

    def _draw_crosshair(self, r: Renderer):
        cs      = self._settings.get("crosshair", {})
        style   = cs.get("style",        "cross")
        color   = cs.get("color",        "green")
        size    = cs.get("size",         10)
        thick   = cs.get("thickness",    2)
        gap     = cs.get("gap",          4)
        outline = cs.get("outline_size", 1)

        fg  = _COLOR_MAP.get(color, _COLOR_MAP["green"])
        bg  = _BLACK

        cx  = self._sw * 0.5
        cy  = self._sh * 0.5

        has_cross  = style in ("cross",  "dot_cross")
        has_dot    = style in ("dot",    "dot_cross", "circle_dot")
        has_circle = style in ("circle", "circle_dot")

        def draw_cross(col, w):
            r.draw_line(cx - gap - size, cy, cx - gap,        cy, w, col)
            r.draw_line(cx + gap,        cy, cx + gap + size, cy, w, col)
            r.draw_line(cx, cy - gap - size, cx, cy - gap,        w, col)
            r.draw_line(cx, cy + gap,        cx, cy + gap + size, w, col)

        def draw_dot(col, radius):
            r.draw_circle_filled(cx, cy, radius, col)

        def draw_circle(col, w):
            r.draw_circle(cx, cy, size, col, thickness=w)

        if outline > 0:
            ow = thick + outline * 2
            if has_cross:  draw_cross(bg, ow)
            if has_circle: draw_circle(bg, ow)
            if has_dot:    draw_dot(bg, thick + outline)

        if has_cross:  draw_cross(fg, thick)
        if has_circle: draw_circle(fg, thick)
        if has_dot:    draw_dot(fg, thick)

    def _draw_module_indicators(self, r: Renderer):
        cs     = self._settings.get("crosshair", {})
        color  = cs.get("color", "green")
        fg     = _COLOR_MAP.get(color, _COLOR_MAP["green"])

        labels = []
        if self._settings.get("recoil",    {}).get("enabled", False):
            labels.append("R")
        if self._settings.get("rapidfire", {}).get("enabled", False):
            labels.append("RF")
        if not labels:
            return

        text = "  ".join(labels)

        ind      = self._settings.get("indicator", {})
        position = ind.get("position", "below_crosshair")

        font_size = 12
        text_w    = len(text) * 8   # rough width estimate (~8px/char at 12pt)
        text_h    = font_size + 4

        # Center-point math shared with crosshair positioning — anchors
        # above/below-crosshair options to screen-center regardless of
        # whether the crosshair itself is currently drawn.
        cx = self._sw * 0.5
        cy = self._sh * 0.5

        # Corner anchoring mirrors _draw_stats's corner logic.
        if "left" in position:
            x = self._MARGIN
        elif "right" in position:
            x = self._sw - self._MARGIN - text_w
        else:
            x = cx - text_w * 0.5

        if "top" in position:
            y = self._MARGIN
        elif "bottom" in position:
            y = self._sh - self._MARGIN - text_h
        elif position == "above_crosshair":
            y = cy - 30 - text_h
        else:   # below_crosshair (and fallback for unrecognized values)
            y = cy + 30

        # Shadow
        r.draw_text(text, x + 1, y + 1, _BLACK, font_size=font_size)
        r.draw_text(text, x,     y,     fg,     font_size=font_size)

    # ------------------------------------------------------------------
    # Running indicator — "R9Tools is loaded" badge
    # ------------------------------------------------------------------

    def _draw_running_indicator(self, r: Renderer):
        """Small power-button badge with 'R9' centered inside, fixed to the
        top-right corner. Standalone "app is alive" status badge, not tied
        to recoil/rapidfire state (see _draw_module_indicators for those).
        Turns red with an "Input Failed" label when self._input_failed is
        set — the only passive signal left once input capture is dead
        (see input_failed_notice.py for the active notice/quit flow)."""
        radius    = 20.0
        thickness = 2.0
        pad       = 6.0   # keep the ring's own margin off the screen edge

        cx = self._sw - self._MARGIN - radius - pad
        cy = self._MARGIN + radius + pad

        fg = _COLOR_MAP["red"] if self._input_failed else _WHITE
        bg = _BLACK

        # Power-symbol ring: small gap centered on straight-up, plus a short
        # stick poking up through the gap (draw_arc's 0 deg = 12 o'clock,
        # clockwise — see dx11_renderer.Renderer.draw_arc).
        gap_half   = 30.0
        stick_top  = cy - radius * 1.3
        stick_base = cy - radius * 0.35

        def draw_icon(col, w):
            r.draw_arc(cx, cy, radius, gap_half, 360.0 - gap_half, col, thickness=w)
            r.draw_line(cx, stick_base, cx, stick_top, w, col)

        # Outline pass (readability against bright/busy backgrounds)
        draw_icon(bg, thickness + 2.0)
        draw_icon(fg, thickness)

        # "R9" label, centered inside the ring, sitting just below the stick
        # so the two don't overlap.
        font_size = 9
        text      = "R9"
        text_w, text_h = r.measure_text(text, font_size=font_size, font_face="Segoe UI")
        tx = cx - text_w * 0.5
        ty = cy - text_h * 0.5 + radius * 0.28

        r.draw_text(text, tx + 1, ty + 1, bg, font_size=font_size, font_face="Segoe UI")
        r.draw_text(text, tx,     ty,     fg, font_size=font_size, font_face="Segoe UI")

        # "Input Failed" label, centered under the badge — only drawn once
        # input has actually failed (see set_input_failed()).
        if self._input_failed:
            fail_text = "Input Failed"
            fail_size = 10
            fw, fh = r.measure_text(fail_text, font_size=fail_size, font_face="Segoe UI")
            fx = cx - fw * 0.5
            fy = cy + radius + pad + 2.0

            r.draw_text(fail_text, fx + 1, fy + 1, bg, font_size=fail_size, font_face="Segoe UI")
            r.draw_text(fail_text, fx,     fy,     fg, font_size=fail_size, font_face="Segoe UI")

    # ------------------------------------------------------------------
    # Stats HUD
    # ------------------------------------------------------------------

    _PAD_X  = 8
    _PAD_Y  = 6
    _LINE_H = 18
    _WIN_W  = 210
    _MARGIN = 12

    def _visible_lines(self, st: dict, data: dict) -> list:
        out = []
        if st.get("show_fps") and "fps" in data:
            out.append(("FPS", f"{data['fps']:.0f}"))

        if st.get("show_cpu_usage") and "cpu_usage" in data:
            val = f"{data['cpu_usage']:.0f}%"
            if st.get("show_cpu_temp") and "cpu_temp" in data:
                val += f"  {data['cpu_temp']:.0f}°C"
            out.append(("CPU", val))
        elif st.get("show_cpu_temp") and "cpu_temp" in data:
            out.append(("CPU TEMP", f"{data['cpu_temp']:.0f}°C"))

        if st.get("show_gpu_usage") and "gpu_usage" in data:
            val = f"{data['gpu_usage']:.0f}%"
            if st.get("show_gpu_temp") and "gpu_temp" in data:
                val += f"  {data['gpu_temp']:.0f}°C"
            out.append(("GPU", val))
        elif st.get("show_gpu_temp") and "gpu_temp" in data:
            out.append(("GPU TEMP", f"{data['gpu_temp']:.0f}°C"))

        if st.get("show_gpu_vram") and "gpu_vram_used" in data:
            if "gpu_vram_total" in data:
                out.append(("VRAM",
                    f"{data['gpu_vram_used']:.1f}/{data['gpu_vram_total']:.1f} GB"))
            else:
                out.append(("VRAM", f"{data['gpu_vram_used']:.1f} GB"))

        if st.get("show_ram") and "ram_used" in data:
            if "ram_total" in data:
                out.append(("RAM",
                    f"{data['ram_used']:.1f}/{data['ram_total']:.1f} GB"))
            else:
                out.append(("RAM", f"{data['ram_used']:.1f} GB"))

        return out

    def _draw_stats(self, r: Renderer, st: dict, data: dict):
        lines = self._visible_lines(st, data)
        if not lines:
            return

        bg_alpha  = st.get("bg_alpha", 70) / 100.0
        text_col  = _hex_to_col(st.get("text_color", "#ffffff"))
        corner    = st.get("corner", "top_right")

        n_lines = len(lines)
        box_w   = self._WIN_W
        box_h   = self._PAD_Y * 2 + n_lines * self._LINE_H

        # Position
        if "left" in corner:
            bx = self._MARGIN
        else:
            bx = self._sw - box_w - self._MARGIN

        if "top" in corner:
            by = self._MARGIN
        elif "bottom" in corner:
            by = self._sh - box_h - self._MARGIN
        else:
            by = (self._sh - box_h) // 2

        # Dark navy theme: background darkens with bg_alpha (0=subtle, 100=solid black)
        # bg_alpha blends from a lighter navy to near-black.
        t = 1.0 - bg_alpha   # 0 at max opacity, 1 at min opacity
        bg_col     = (0.055 + t * 0.12,  0.065 + t * 0.13,  0.090 + t * 0.14,  1.0)
        shadow_col = (0.020 + t * 0.05,  0.025 + t * 0.05,  0.035 + t * 0.06,  1.0)
        dim_col    = (0.480,              0.620,              0.750,              1.0)  # cool blue-grey labels

        # Drop shadow (solid rect, offset 4px right and down, like Qt version)
        _SH = 4.0
        r.draw_line(bx + _SH, by + _SH + box_h * 0.5,
                    bx + _SH + box_w, by + _SH + box_h * 0.5,
                    float(box_h), shadow_col)

        # Background fill (one thick horizontal line = filled rect)
        r.draw_line(bx, by + box_h * 0.5,
                    bx + box_w, by + box_h * 0.5,
                    float(box_h), bg_col)

        # Text lines
        y = float(by + self._PAD_Y)
        for label, value in lines:
            lw = r.draw_text(label + "  ", bx + self._PAD_X, y,
                             dim_col, font_size=11, font_face="Segoe UI")
            r.draw_text(value, bx + self._PAD_X + lw, y,
                        text_col, font_size=11, font_face="Segoe UI")
            y += self._LINE_H

    # ------------------------------------------------------------------
    # Strength indicator
    # ------------------------------------------------------------------

    def _draw_si(self, r: Renderer):
        text = str(self._si_value)
        cx   = self._sw * 0.5
        cy   = self._sh * 0.5
        fs   = 16
        # Approximate centering: ~9px per char at size 16
        x    = cx - len(text) * 4.5
        y    = cy - fs

        # Shadow
        r.draw_text(text, x + 1, y + 1, _BLACK, font_size=fs)
        r.draw_text(text, x,     y,     _WHITE, font_size=fs)

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    def _handle_display_change(self):
        """Re-query the (possibly changed) primary-monitor resolution and
        resize/reposition the window to match. Dropping the SWP_NOMOVE/
        SWP_NOSIZE flags used elsewhere means this actually changes the
        window's size, synchronously dispatching WM_SIZE back into
        _wnd_proc, which drives the swap chain/RTV/viewport rebuild via
        the existing _on_resize() path."""
        sw = _user32.GetSystemMetrics(_SM_CXSCREEN)
        sh = _user32.GetSystemMetrics(_SM_CYSCREEN)
        if sw <= 0 or sh <= 0:
            return
        _SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, sw, sh, _SWP_NOACTIVATE)

    def _on_resize(self, w: int, h: int):
        self._sw = w
        self._sh = h
        if self._rtv:
            dx._release(self._rtv)
            self._rtv = 0
        dx.swap_resize(self._swap_chain, w, h)
        self._rtv = dx.make_rtv(self._device, self._swap_chain)
        if self._renderer:
            self._renderer.resize(w, h)
        dx.ctx_set_viewport(self._context, float(w), float(h))
        dx.ctx_set_rtv(self._context, self._rtv)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def _teardown(self):
        if self._renderer:
            self._renderer.release()
            self._renderer = None
        for attr in ("_rtv", "_swap_chain", "_context", "_device"):
            v = getattr(self, attr, 0)
            if v:
                dx._release(v)
                setattr(self, attr, 0)
        # DComp objects must be released before the window is destroyed
        for attr in ("_dcomp_visual", "_dcomp_target", "_dcomp_device"):
            v = getattr(self, attr, 0)
            if v:
                dx._release(v)
                setattr(self, attr, 0)
        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            _user32.UnregisterClassW(self._CLASS_NAME,
                                      _kernel32.GetModuleHandleW(None))
            self._hwnd = 0
