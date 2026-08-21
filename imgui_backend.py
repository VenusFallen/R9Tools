"""
imgui_backend.py — Win32 input + Direct3D 11 renderer for imgui-bundle.

imgui-bundle does not ship Win32/DX11 backends in Python.  This module
implements both from scratch using ctypes + our existing dx11_bridge helpers.

Usage:
    backend = ImguiBackend(hwnd, device, context)
    backend.init()
    # each frame:
    backend.new_frame()
    imgui.new_frame()
    # ... draw calls ...
    imgui.render()
    backend.render(imgui.get_draw_data())
    # on resize:
    backend.set_display_size(w, h)
    # on shutdown:
    backend.shutdown()
"""
import ctypes
import ctypes.wintypes as wintypes
import struct
import math
from ctypes import c_int, c_uint, c_float, c_void_p, c_size_t, POINTER, byref
from ctypes import c_ubyte, c_ushort

from imgui_bundle import imgui
import dx11_bridge as dx


# ---------------------------------------------------------------------------
# Win32 raw input constants for mouse / keyboard
# ---------------------------------------------------------------------------

_WM_MOUSEMOVE       = 0x0200
_WM_LBUTTONDOWN     = 0x0201
_WM_LBUTTONUP       = 0x0202
_WM_RBUTTONDOWN     = 0x0204
_WM_RBUTTONUP       = 0x0205
_WM_MBUTTONDOWN     = 0x0207
_WM_MBUTTONUP       = 0x0208
_WM_XBUTTONDOWN     = 0x020B
_WM_XBUTTONUP       = 0x020C
_WM_MOUSEWHEEL      = 0x020A
_WM_MOUSEHWHEEL     = 0x020E
_WM_KEYDOWN         = 0x0100
_WM_KEYUP           = 0x0101
_WM_SYSKEYDOWN      = 0x0104
_WM_SYSKEYUP        = 0x0105
_WM_CHAR            = 0x0102
_WM_SETCURSOR       = 0x0020

_MK_XBUTTON1        = 0x0020
_MK_XBUTTON2        = 0x0040
_WHEEL_DELTA        = 120

# VK → imgui.Key mapping (only the keys imgui cares about)
_VK_TO_IMGUI = {
    0x08: imgui.Key.backspace,
    0x09: imgui.Key.tab,
    0x0D: imgui.Key.enter,
    0x1B: imgui.Key.escape,
    0x20: imgui.Key.space,
    0x21: imgui.Key.page_up,
    0x22: imgui.Key.page_down,
    0x23: imgui.Key.end,
    0x24: imgui.Key.home,
    0x25: imgui.Key.left_arrow,
    0x26: imgui.Key.up_arrow,
    0x27: imgui.Key.right_arrow,
    0x28: imgui.Key.down_arrow,
    0x2E: imgui.Key.delete,
    0x10: imgui.Key.left_shift,
    0x11: imgui.Key.left_ctrl,
    0x12: imgui.Key.left_alt,
    0xA0: imgui.Key.left_shift,
    0xA1: imgui.Key.right_shift,
    0xA2: imgui.Key.left_ctrl,
    0xA3: imgui.Key.right_ctrl,
    0xA4: imgui.Key.left_alt,
    0xA5: imgui.Key.right_alt,
    0x5B: imgui.Key.left_super,
    0x5C: imgui.Key.right_super,
    0x2C: imgui.Key.print_screen,
    0x2D: imgui.Key.insert,
    0x60: imgui.Key.keypad0,
    0x61: imgui.Key.keypad1,
    0x62: imgui.Key.keypad2,
    0x63: imgui.Key.keypad3,
    0x64: imgui.Key.keypad4,
    0x65: imgui.Key.keypad5,
    0x66: imgui.Key.keypad6,
    0x67: imgui.Key.keypad7,
    0x68: imgui.Key.keypad8,
    0x69: imgui.Key.keypad9,
    0x6A: imgui.Key.keypad_multiply,
    0x6B: imgui.Key.keypad_add,
    0x6D: imgui.Key.keypad_subtract,
    0x6E: imgui.Key.keypad_decimal,
    0x6F: imgui.Key.keypad_divide,
    0x70: imgui.Key.f1,  0x71: imgui.Key.f2,  0x72: imgui.Key.f3,
    0x73: imgui.Key.f4,  0x74: imgui.Key.f5,  0x75: imgui.Key.f6,
    0x76: imgui.Key.f7,  0x77: imgui.Key.f8,  0x78: imgui.Key.f9,
    0x79: imgui.Key.f10, 0x7A: imgui.Key.f11, 0x7B: imgui.Key.f12,
    0xBC: imgui.Key.comma,
    0xBE: imgui.Key.period,
    0xBF: imgui.Key.slash,
    0xBA: imgui.Key.semicolon,
    0xBB: imgui.Key.equal,
    0xBD: imgui.Key.minus,
    0xDB: imgui.Key.left_bracket,
    0xDD: imgui.Key.right_bracket,
    0xDC: imgui.Key.backslash,
    0xC0: imgui.Key.grave_accent,
    0xDE: imgui.Key.apostrophe,
}
# A-Z and 0-9
for _vk in range(0x30, 0x3A):   # '0'-'9'
    _VK_TO_IMGUI[_vk] = getattr(imgui.Key, f"_{_vk - 0x30}", None) or imgui.Key.none
for _vk in range(0x41, 0x5B):   # 'A'-'Z'
    _VK_TO_IMGUI[_vk] = getattr(imgui.Key, chr(_vk).lower(), None) or imgui.Key.none


# ---------------------------------------------------------------------------
# DX11 shader source (imgui vertex format)
# ImDrawVert: float2 pos, float2 uv, uint col (RGBA packed)
# Output is premultiplied for DComp swap chain.
# ---------------------------------------------------------------------------

_VS_SRC = r"""
cbuffer CB : register(b0) {
    float4x4 proj;
};
// ImDrawVert: float2 pos, float2 uv, R8G8B8A8_UNORM col (GPU auto-converts to float4)
struct VSIn  { float2 pos : POSITION; float2 uv : TEXCOORD0; float4 col : COLOR0; };
struct VSOut { float4 pos : SV_POSITION; float4 col : COLOR0; float2 uv : TEXCOORD0; };
VSOut main(VSIn v) {
    VSOut o;
    o.pos = mul(proj, float4(v.pos, 0.0, 1.0));
    o.col = v.col;
    o.uv  = v.uv;
    return o;
}
"""

_PS_SRC = r"""
Texture2D    tex : register(t0);
SamplerState sam : register(s0);
struct PSIn { float4 pos : SV_POSITION; float4 col : COLOR0; float2 uv : TEXCOORD0; };
float4 main(PSIn p) : SV_TARGET {
    float4 c = p.col * tex.Sample(sam, p.uv);
    // Premultiply for DComp premultiplied-alpha swap chain
    return float4(c.rgb * c.a, c.a);
}
"""


# ---------------------------------------------------------------------------
# D3D11 structures needed here (beyond what dx11_bridge exports)
# ---------------------------------------------------------------------------

_DXGI_FORMAT_R32G32_FLOAT       = 16
_DXGI_FORMAT_R32G32B32A32_FLOAT = 2
_DXGI_FORMAT_R8G8B8A8_UNORM     = 28
_DXGI_FORMAT_R8_UNORM           = 61

_BIND_VERTEX_BUFFER    = 0x1
_BIND_INDEX_BUFFER     = 0x2
_BIND_CONSTANT_BUFFER  = 0x4
_BIND_SHADER_RESOURCE  = 0x8

_USAGE_DYNAMIC  = 2
_USAGE_DEFAULT  = 0
_CPU_WRITE      = 0x10000
_MAP_WRITE_DISCARD = 4

_BLEND_SRC_ALPHA     = 5
_BLEND_INV_SRC_ALPHA = 6
_BLEND_ONE           = 2
_BLEND_OP_ADD        = 1

_INPUT_PER_VERTEX = 0
_FILL_SOLID       = 3
_CULL_NONE        = 1


class _BufDesc(ctypes.Structure):
    _fields_ = [
        ("ByteWidth",           c_uint),
        ("Usage",               c_uint),
        ("BindFlags",           c_uint),
        ("CPUAccessFlags",      c_uint),
        ("MiscFlags",           c_uint),
        ("StructureByteStride", c_uint),
    ]


class _Tex2DDesc(ctypes.Structure):
    _fields_ = [
        ("Width",          c_uint),
        ("Height",         c_uint),
        ("MipLevels",      c_uint),
        ("ArraySize",      c_uint),
        ("Format",         c_uint),
        ("SampleDesc",     dx._SampleDesc),
        ("Usage",          c_uint),
        ("BindFlags",      c_uint),
        ("CPUAccessFlags", c_uint),
        ("MiscFlags",      c_uint),
    ]


class _SubresData(ctypes.Structure):
    _fields_ = [
        ("pSysMem",          c_void_p),
        ("SysMemPitch",      c_uint),
        ("SysMemSlicePitch", c_uint),
    ]


class _InputElement(ctypes.Structure):
    _fields_ = [
        ("SemanticName",         ctypes.c_char_p),
        ("SemanticIndex",        c_uint),
        ("Format",               c_uint),
        ("InputSlot",            c_uint),
        ("AlignedByteOffset",    c_uint),
        ("InputSlotClass",       c_uint),
        ("InstanceDataStepRate", c_uint),
    ]


class _BlendDesc(ctypes.Structure):
    class _RT(ctypes.Structure):
        _fields_ = [
            ("BlendEnable",           c_int),
            ("SrcBlend",              c_uint),
            ("DestBlend",             c_uint),
            ("BlendOp",               c_uint),
            ("SrcBlendAlpha",         c_uint),
            ("DestBlendAlpha",        c_uint),
            ("BlendOpAlpha",          c_uint),
            ("RenderTargetWriteMask", c_ubyte),
        ]
    _fields_ = [
        ("AlphaToCoverageEnable",  c_int),
        ("IndependentBlendEnable", c_int),
        ("RenderTarget",           _RT * 8),
    ]


class _RastDesc(ctypes.Structure):
    _fields_ = [
        ("FillMode",              c_uint),
        ("CullMode",              c_uint),
        ("FrontCounterClockwise", c_int),
        ("DepthBias",             c_int),
        ("DepthBiasClamp",        c_float),
        ("SlopeScaledDepthBias",  c_float),
        ("DepthClipEnable",       c_int),
        ("ScissorEnable",         c_int),
        ("MultisampleEnable",     c_int),
        ("AntialiasedLineEnable", c_int),
    ]


class _DepthStencilDesc(ctypes.Structure):
    _fields_ = [
        ("DepthEnable",      c_int),
        ("DepthWriteMask",   c_uint),
        ("DepthFunc",        c_uint),
        ("StencilEnable",    c_int),
        ("StencilReadMask",  c_ubyte),
        ("StencilWriteMask", c_ubyte),
        ("FrontFace",        c_ubyte * 8),
        ("BackFace",         c_ubyte * 8),
    ]


class _ScissorRect(ctypes.Structure):
    _fields_ = [("left", c_int), ("top", c_int), ("right", c_int), ("bottom", c_int)]


class _MappedSubresource(ctypes.Structure):
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


# ---------------------------------------------------------------------------
# Device vtable slots (same as in dx11_renderer.py)
# ---------------------------------------------------------------------------

_DEV = {
    "CreateBuffer":             3,
    "CreateTexture2D":          5,
    "CreateShaderResourceView": 7,
    "CreateRenderTargetView":   9,
    "CreateInputLayout":        11,
    "CreateVertexShader":       12,
    "CreatePixelShader":        15,
    "CreateBlendState":         20,
    "CreateRasterizerState":    22,
    "CreateSamplerState":       23,
    "CreateDepthStencilState":  21,
}

_CTX = {
    "VSSetConstantBuffers":  7,
    "PSSetShaderResources":  8,
    "PSSetShader":           9,
    "PSSetSamplers":        10,
    "VSSetShader":          11,
    "Draw":                 13,
    "DrawIndexed":          12,
    "Map":                  14,
    "Unmap":                15,
    "PSSetConstantBuffers": 16,
    "IASetInputLayout":     17,
    "IASetVertexBuffers":   18,
    "IASetIndexBuffer":     19,
    "IASetPrimitiveTopology": 24,
    "OMSetRenderTargets":   33,
    "OMSetBlendState":      35,
    "OMSetDepthStencilState": 36,
    "RSSetState":           43,
    "RSSetViewports":       44,
    "RSSetScissorRects":    45,
    "ClearRenderTargetView": 50,
}


def _dev(dev, name, res, argt, *args):
    return dx._com(dev, _DEV[name], res, argt, *args)


def _ctx(ctx, name, res, argt, *args):
    return dx._com(ctx, _CTX[name], res, argt, *args)


# ---------------------------------------------------------------------------
# D3DCompile
# ---------------------------------------------------------------------------

_d3dcompiler = ctypes.windll.LoadLibrary("d3dcompiler_47.dll")
_D3DCompile  = _d3dcompiler.D3DCompile
_D3DCompile.restype  = c_int
_D3DCompile.argtypes = [
    c_void_p, c_size_t, ctypes.c_char_p, c_void_p, c_void_p,
    ctypes.c_char_p, ctypes.c_char_p, c_uint, c_uint,
    POINTER(c_void_p), POINTER(c_void_p),
]


def _compile(src: str, entry: str, target: str) -> bytes:
    src_b = src.encode()
    code  = c_void_p()
    errs  = c_void_p()
    hr = _D3DCompile(src_b, len(src_b), None, None, None,
                     entry.encode(), target.encode(), 0, 0,
                     byref(code), byref(errs))
    if hr < 0:
        msg = ""
        if errs.value:
            ptr  = dx._com(errs.value, 3, c_void_p, [])
            size = dx._com(errs.value, 4, c_size_t, [])
            msg  = bytes((c_ubyte * size).from_address(ptr)).decode(errors="replace")
            dx._release(errs.value)
        raise RuntimeError(f"Shader compile failed: {msg}")
    if errs.value:
        dx._release(errs.value)
    ptr  = dx._com(code.value, 3, c_void_p, [])
    size = dx._com(code.value, 4, c_size_t, [])
    data = bytes((c_ubyte * size).from_address(ptr))
    dx._release(code.value)
    return data


# ---------------------------------------------------------------------------
# ImguiBackend
# ---------------------------------------------------------------------------

_VERTEX_SIZE  = 20   # float2 pos + float2 uv + uint32 col
_INDEX_SIZE   = 2    # uint16


class ImguiBackend:
    """
    Minimal Win32 + DX11 backend for imgui-bundle.

    Win32 input: call process_win32_message(msg, wparam, lparam) from your WndProc.
    DX11 render: call render(draw_data, rtv) each frame after imgui.render().
    """

    def __init__(self, hwnd: int, device: int, context: int):
        self._hwnd    = hwnd
        self._dev     = device
        self._ctx     = context
        self._w       = 0
        self._h       = 0

        # DX11 objects
        self._vs      = 0
        self._ps      = 0
        self._il      = 0
        self._cb      = 0   # projection matrix constant buffer
        self._vb      = 0
        self._ib      = 0
        self._blend   = 0
        self._rast    = 0
        self._ds      = 0   # depth-stencil state (depth disabled)
        self._sampler = 0
        self._vb_cap  = 0
        self._ib_cap  = 0

        # Texture cache: small index → SRV ptr; imgui unique_id → index
        self._srv_table: list[int] = [0]   # index 0 = null/unset
        self._uid_to_idx: dict[int, int] = {}
        self._font_srv: int = 0            # fallback SRV when tex_id is 0

        # Time tracking
        import time
        self._last_time = time.monotonic()

        # Cursor position
        self._last_mx = 0
        self._last_my = 0

    def init(self):
        """Build all DX11 pipeline objects and configure imgui IO."""
        imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self._build_shaders()
        self._build_states()
        self._build_cb()
        self._alloc_vb(5000)
        self._alloc_ib(10000)

    def set_display_size(self, w: int, h: int):
        self._w = w
        self._h = h
        imgui.get_io().display_size = (float(w), float(h))

    # ------------------------------------------------------------------
    # Win32 input → imgui IO
    # ------------------------------------------------------------------

    def process_win32_message(self, msg: int, wparam: int, lparam: int) -> bool:
        """
        Feed a Win32 message into imgui IO.
        Returns True if imgui consumed the event (caller may skip default processing).
        """
        io = imgui.get_io()

        if msg == _WM_MOUSEMOVE:
            mx = ctypes.c_short(lparam & 0xFFFF).value
            my = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            io.add_mouse_pos_event(float(mx), float(my))
            return False

        if msg in (_WM_LBUTTONDOWN, _WM_LBUTTONUP):
            io.add_mouse_button_event(0, msg == _WM_LBUTTONDOWN)
            return io.want_capture_mouse

        if msg in (_WM_RBUTTONDOWN, _WM_RBUTTONUP):
            io.add_mouse_button_event(1, msg == _WM_RBUTTONDOWN)
            return io.want_capture_mouse

        if msg in (_WM_MBUTTONDOWN, _WM_MBUTTONUP):
            io.add_mouse_button_event(2, msg == _WM_MBUTTONDOWN)
            return io.want_capture_mouse

        if msg in (_WM_XBUTTONDOWN, _WM_XBUTTONUP):
            btn = 3 if (wparam >> 16) & 0xFFFF == 1 else 4
            io.add_mouse_button_event(btn, msg == _WM_XBUTTONDOWN)
            return io.want_capture_mouse

        if msg == _WM_MOUSEWHEEL:
            delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            io.add_mouse_wheel_event(0.0, delta / _WHEEL_DELTA)
            return io.want_capture_mouse

        if msg == _WM_MOUSEHWHEEL:
            delta = ctypes.c_short((wparam >> 16) & 0xFFFF).value
            io.add_mouse_wheel_event(delta / _WHEEL_DELTA, 0.0)
            return io.want_capture_mouse

        if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            vk   = wparam & 0xFF
            key  = _VK_TO_IMGUI.get(vk, imgui.Key.none)
            if key != imgui.Key.none:
                io.add_key_event(key, True)
            return io.want_capture_keyboard

        if msg in (_WM_KEYUP, _WM_SYSKEYUP):
            vk   = wparam & 0xFF
            key  = _VK_TO_IMGUI.get(vk, imgui.Key.none)
            if key != imgui.Key.none:
                io.add_key_event(key, False)
            return io.want_capture_keyboard

        if msg == _WM_CHAR:
            ch = wparam & 0xFFFF
            if ch > 0:
                io.add_input_character(ch)
            return io.want_capture_keyboard

        return False

    # ------------------------------------------------------------------
    # Per-frame
    # ------------------------------------------------------------------

    def new_frame(self):
        """Call before imgui.new_frame() each loop iteration."""
        import time
        now = time.monotonic()
        io  = imgui.get_io()
        io.delta_time = max(float(now - self._last_time), 1e-4)
        self._last_time = now

        # Update display size from window client rect each frame
        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rc = _RECT()
        if ctypes.windll.user32.GetClientRect(self._hwnd, ctypes.byref(rc)):
            w = rc.right - rc.left
            h = rc.bottom - rc.top
            if w > 0 and h > 0:
                io.display_size = (float(w), float(h))
                self._w, self._h = w, h

        # Update modifier keys
        GetAsyncKeyState = ctypes.windll.user32.GetAsyncKeyState
        io.add_key_event(imgui.Key.mod_ctrl,  bool(GetAsyncKeyState(0x11) & 0x8000))
        io.add_key_event(imgui.Key.mod_shift, bool(GetAsyncKeyState(0x10) & 0x8000))
        io.add_key_event(imgui.Key.mod_alt,   bool(GetAsyncKeyState(0x12) & 0x8000))

        # NOTE: _update_textures() must be called AFTER imgui.new_frame() each frame.
        # It is called from OverlayApp._render_frame() immediately after imgui.new_frame().

    # ------------------------------------------------------------------
    # Texture management (renderer_has_textures API)
    # ------------------------------------------------------------------

    def _update_textures(self):
        """Process pending texture create/update/destroy requests (renderer_has_textures API).

        tex_id is stored as a small table index (fits in C++ int) rather than the raw
        64-bit pointer, since nanobind's set_tex_id binding uses C int internally.
        The actual SRV is looked up from self._srv_table[idx] at draw time.
        """
        fa = imgui.get_io().fonts
        for t in fa.tex_list:
            st = t.status
            if st == imgui.ImTextureStatus.want_create:
                srv = self._upload_texture(t)
                idx = len(self._srv_table)
                self._srv_table.append(srv)
                self._uid_to_idx[t.unique_id] = idx
                if not self._font_srv:
                    self._font_srv = srv
                t.set_tex_id(idx)   # small int — fits in C int
                t.set_status(imgui.ImTextureStatus.ok)
            elif st == imgui.ImTextureStatus.want_updates:
                idx = self._uid_to_idx.get(t.unique_id, 0)
                if idx and idx < len(self._srv_table) and self._srv_table[idx]:
                    dx._release(self._srv_table[idx])
                srv = self._upload_texture(t)
                if idx:
                    self._srv_table[idx] = srv
                else:
                    idx = len(self._srv_table)
                    self._srv_table.append(srv)
                    self._uid_to_idx[t.unique_id] = idx
                if not self._font_srv:
                    self._font_srv = srv
                t.set_tex_id(idx)
                t.set_status(imgui.ImTextureStatus.ok)
            elif st == imgui.ImTextureStatus.want_destroy:
                idx = self._uid_to_idx.pop(t.unique_id, 0)
                if idx and idx < len(self._srv_table) and self._srv_table[idx]:
                    dx._release(self._srv_table[idx])
                    self._srv_table[idx] = 0
                t.set_tex_id(0)
                t.set_status(imgui.ImTextureStatus.destroyed)

    def _upload_texture(self, t) -> int:
        """Upload ImTextureData pixels to a D3D11 SRV and return the SRV pointer."""
        import numpy as np
        w   = t.width
        h   = t.height
        bpp = t.bytes_per_pixel
        fmt = _DXGI_FORMAT_R8G8B8A8_UNORM if bpp == 4 else _DXGI_FORMAT_R8_UNORM
        arr = np.ascontiguousarray(t.get_pixels_array(), dtype=np.uint8)

        td = _Tex2DDesc()
        td.Width              = w
        td.Height             = h
        td.MipLevels          = 1
        td.ArraySize          = 1
        td.Format             = fmt
        td.SampleDesc.Count   = 1
        td.SampleDesc.Quality = 0
        td.Usage              = _USAGE_DEFAULT
        td.BindFlags          = _BIND_SHADER_RESOURCE
        td.CPUAccessFlags     = 0
        td.MiscFlags          = 0

        sd = _SubresData()
        sd.pSysMem            = arr.ctypes.data_as(c_void_p)
        sd.SysMemPitch        = w * bpp
        sd.SysMemSlicePitch   = 0

        tex = c_void_p()
        hr  = _dev(self._dev, "CreateTexture2D", c_int,
                   [POINTER(_Tex2DDesc), POINTER(_SubresData), POINTER(c_void_p)],
                   byref(td), byref(sd), byref(tex))
        dx._check(hr, "CreateTexture2D (imgui tex)")

        srv = c_void_p()
        hr  = _dev(self._dev, "CreateShaderResourceView", c_int,
                   [c_void_p, c_void_p, POINTER(c_void_p)],
                   c_void_p(tex.value), None, byref(srv))
        dx._check(hr, "CreateSRV (imgui tex)")
        dx._release(tex.value)

        return int(srv.value) if srv.value else 0

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, draw_data, rtv: int):
        """Render imgui draw data into *rtv*."""
        if not draw_data.valid or draw_data.total_vtx_count == 0:
            return

        ctx = self._ctx
        w   = draw_data.display_size.x
        h   = draw_data.display_size.y
        if w <= 0 or h <= 0:
            return

        # Upload projection matrix
        L = draw_data.display_pos.x
        R = draw_data.display_pos.x + w
        T = draw_data.display_pos.y
        B = draw_data.display_pos.y + h
        proj = struct.pack("16f",
            2/(R-L),     0,           0, 0,
            0,           2/(T-B),     0, 0,
            0,           0,           0.5, 0,
            (R+L)/(L-R), (T+B)/(B-T), 0.5, 1,
        )
        self._map_write(self._cb, proj)

        # Grow vertex/index buffers if needed
        n_vtx = draw_data.total_vtx_count + 5000
        n_idx = draw_data.total_idx_count + 10000
        if n_vtx > self._vb_cap:
            self._alloc_vb(n_vtx)
        if n_idx > self._ib_cap:
            self._alloc_ib(n_idx)

        # Pack all vertices and indices into flat byte arrays
        # ImDrawVert layout: float2 pos (8) + float2 uv (8) + uint32 col (4) = 20 bytes
        vtx_bytes = bytearray()
        idx_bytes = bytearray()
        for cl in draw_data.cmd_lists:
            for v in cl.vtx_buffer:
                vtx_bytes += struct.pack("ffff I", v.pos.x, v.pos.y, v.uv.x, v.uv.y, v.col)
            for i in cl.idx_buffer:
                idx_bytes += struct.pack("H", i)

        # Upload
        self._map_write(self._vb, bytes(vtx_bytes))
        self._map_write(self._ib, bytes(idx_bytes))

        # Set state
        rtv_arr = (c_void_p * 1)(rtv)
        _ctx(ctx, "OMSetRenderTargets", None,
             [c_uint, POINTER(c_void_p * 1), c_void_p], 1, rtv_arr, None)

        vp = dx.D3D11Viewport(0, 0, w, h, 0.0, 1.0)
        vp_arr = (dx.D3D11Viewport * 1)(vp)
        _ctx(ctx, "RSSetViewports", None,
             [c_uint, POINTER(dx.D3D11Viewport * 1)], 1, vp_arr)

        stride = c_uint(_VERTEX_SIZE)
        offset = c_uint(0)
        vb_arr = (c_void_p * 1)(self._vb)
        _ctx(ctx, "IASetVertexBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1), POINTER(c_uint), POINTER(c_uint)],
             0, 1, vb_arr, byref(stride), byref(offset))
        _ctx(ctx, "IASetIndexBuffer", None,
             [c_void_p, c_uint, c_uint],
             c_void_p(self._ib), 0x39, 0)   # 0x39 = DXGI_FORMAT_R16_UINT
        _ctx(ctx, "IASetPrimitiveTopology", None, [c_uint], 4)   # TRIANGLELIST
        _ctx(ctx, "IASetInputLayout", None, [c_void_p], c_void_p(self._il))
        _ctx(ctx, "VSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._vs), None, 0)
        cb_arr = (c_void_p * 1)(self._cb)
        _ctx(ctx, "VSSetConstantBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1)], 0, 1, cb_arr)
        _ctx(ctx, "PSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._ps), None, 0)
        smp_arr = (c_void_p * 1)(self._sampler)
        _ctx(ctx, "PSSetSamplers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1)], 0, 1, smp_arr)
        _ctx(ctx, "OMSetBlendState", None,
             [c_void_p, c_void_p, c_uint], c_void_p(self._blend), None, 0xFFFFFFFF)
        _ctx(ctx, "RSSetState", None, [c_void_p], c_void_p(self._rast))
        _ctx(ctx, "OMSetDepthStencilState", None,
             [c_void_p, c_uint], c_void_p(self._ds), 0)

        # Draw each command
        global_idx_offset = 0
        global_vtx_offset = 0
        clip_off = draw_data.display_pos

        for cl in draw_data.cmd_lists:
            for cmd in cl.cmd_buffer:
                # Scissor rect
                cx0 = int(cmd.clip_rect.x - clip_off.x)
                cy0 = int(cmd.clip_rect.y - clip_off.y)
                cx1 = int(cmd.clip_rect.z - clip_off.x)
                cy1 = int(cmd.clip_rect.w - clip_off.y)
                sr = _ScissorRect(cx0, cy0, cx1, cy1)
                sr_arr = (_ScissorRect * 1)(sr)
                _ctx(ctx, "RSSetScissorRects", None,
                     [c_uint, POINTER(_ScissorRect * 1)], 1, sr_arr)

                # Bind texture — tex_id is a small table index into self._srv_table
                try:
                    idx = int(cmd.tex_ref.get_tex_id()) if cmd.tex_ref else 0
                except Exception:
                    idx = 0
                if idx and idx < len(self._srv_table):
                    tex_srv = self._srv_table[idx] or self._font_srv
                else:
                    tex_srv = self._font_srv
                srv_arr = (c_void_p * 1)(tex_srv)
                _ctx(ctx, "PSSetShaderResources", None,
                     [c_uint, c_uint, POINTER(c_void_p * 1)], 0, 1, srv_arr)

                _ctx(ctx, "DrawIndexed", None,
                     [c_uint, c_uint, c_int],
                     cmd.elem_count,
                     cmd.idx_offset + global_idx_offset,
                     cmd.vtx_offset + global_vtx_offset)

            global_idx_offset += len(cl.idx_buffer)
            global_vtx_offset += len(cl.vtx_buffer)

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    def _build_shaders(self):
        dev = self._dev
        vs_bc = _compile(_VS_SRC, "main", "vs_4_0")
        ps_bc = _compile(_PS_SRC, "main", "ps_4_0")

        vs = c_void_p()
        hr = _dev(dev, "CreateVertexShader", c_int,
                  [c_void_p, c_size_t, c_void_p, POINTER(c_void_p)],
                  vs_bc, len(vs_bc), None, byref(vs))
        dx._check(hr, "imgui CreateVertexShader")
        self._vs = vs.value

        ps = c_void_p()
        hr = _dev(dev, "CreatePixelShader", c_int,
                  [c_void_p, c_size_t, c_void_p, POINTER(c_void_p)],
                  ps_bc, len(ps_bc), None, byref(ps))
        dx._check(hr, "imgui CreatePixelShader")
        self._ps = ps.value

        # Input layout matches ImDrawVert: float2 pos (0), float2 uv (8), uint col (16)
        elems = (_InputElement * 3)(
            _InputElement(b"POSITION", 0, _DXGI_FORMAT_R32G32_FLOAT,    0,  0, _INPUT_PER_VERTEX, 0),
            _InputElement(b"TEXCOORD", 0, _DXGI_FORMAT_R32G32_FLOAT,    0,  8, _INPUT_PER_VERTEX, 0),
            _InputElement(b"COLOR",    0, _DXGI_FORMAT_R8G8B8A8_UNORM,  0, 16, _INPUT_PER_VERTEX, 0),
        )
        il = c_void_p()
        hr = _dev(dev, "CreateInputLayout", c_int,
                  [POINTER(_InputElement * 3), c_uint, c_void_p, c_size_t, POINTER(c_void_p)],
                  elems, 3, vs_bc, len(vs_bc), byref(il))
        dx._check(hr, "imgui CreateInputLayout")
        self._il = il.value

    def _build_states(self):
        dev = self._dev

        # Blend: premultiplied (ONE / INV_SRC_ALPHA) — output already premultiplied in PS
        bd  = _BlendDesc()
        bd.AlphaToCoverageEnable  = 0
        bd.IndependentBlendEnable = 0
        rt  = bd.RenderTarget[0]
        rt.BlendEnable    = 1
        rt.SrcBlend       = _BLEND_ONE
        rt.DestBlend      = _BLEND_INV_SRC_ALPHA
        rt.BlendOp        = _BLEND_OP_ADD
        rt.SrcBlendAlpha  = _BLEND_ONE
        rt.DestBlendAlpha = _BLEND_INV_SRC_ALPHA
        rt.BlendOpAlpha   = _BLEND_OP_ADD
        rt.RenderTargetWriteMask = 0x0F
        bs = c_void_p()
        hr = _dev(dev, "CreateBlendState", c_int,
                  [POINTER(_BlendDesc), POINTER(c_void_p)], byref(bd), byref(bs))
        dx._check(hr, "imgui CreateBlendState")
        self._blend = bs.value

        # Rasterizer: solid, no cull, scissor enabled
        rd = _RastDesc()
        rd.FillMode      = _FILL_SOLID
        rd.CullMode      = _CULL_NONE
        rd.ScissorEnable = 1
        rd.DepthClipEnable = 1
        rs = c_void_p()
        hr = _dev(dev, "CreateRasterizerState", c_int,
                  [POINTER(_RastDesc), POINTER(c_void_p)], byref(rd), byref(rs))
        dx._check(hr, "imgui CreateRasterizerState")
        self._rast = rs.value

        # Depth-stencil: disabled
        dd = _DepthStencilDesc()
        dd.DepthEnable   = 0
        dd.DepthWriteMask = 0
        dd.DepthFunc     = 1   # NEVER
        ds = c_void_p()
        hr = _dev(dev, "CreateDepthStencilState", c_int,
                  [POINTER(_DepthStencilDesc), POINTER(c_void_p)], byref(dd), byref(ds))
        dx._check(hr, "imgui CreateDepthStencilState")
        self._ds = ds.value

        # Sampler: linear, clamp
        from dx11_renderer import _SamplerDesc
        sd = _SamplerDesc()
        sd.Filter    = 21    # MIN_MAG_MIP_LINEAR
        sd.AddressU  = 3     # CLAMP
        sd.AddressV  = 3
        sd.AddressW  = 3
        sd.ComparisonFunc = 1
        sd.MinLOD    = -3.4e38
        sd.MaxLOD    =  3.4e38
        smp = c_void_p()
        hr  = _dev(dev, "CreateSamplerState", c_int,
                   [POINTER(_SamplerDesc), POINTER(c_void_p)], byref(sd), byref(smp))
        dx._check(hr, "imgui CreateSamplerState")
        self._sampler = smp.value

    def _build_cb(self):
        bd = _BufDesc()
        bd.ByteWidth      = 64   # 4x4 float matrix
        bd.Usage          = _USAGE_DYNAMIC
        bd.BindFlags      = _BIND_CONSTANT_BUFFER
        bd.CPUAccessFlags = 0x10000
        cb = c_void_p()
        hr = _dev(self._dev, "CreateBuffer", c_int,
                  [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                  byref(bd), None, byref(cb))
        dx._check(hr, "imgui CreateBuffer (CB)")
        self._cb = cb.value

    def _alloc_vb(self, n: int):
        if self._vb:
            dx._release(self._vb)
        bd = _BufDesc()
        bd.ByteWidth      = _VERTEX_SIZE * n
        bd.Usage          = _USAGE_DYNAMIC
        bd.BindFlags      = _BIND_VERTEX_BUFFER
        bd.CPUAccessFlags = 0x10000
        vb = c_void_p()
        hr = _dev(self._dev, "CreateBuffer", c_int,
                  [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                  byref(bd), None, byref(vb))
        dx._check(hr, "imgui CreateBuffer (VB)")
        self._vb     = vb.value
        self._vb_cap = n

    def _alloc_ib(self, n: int):
        if self._ib:
            dx._release(self._ib)
        bd = _BufDesc()
        bd.ByteWidth      = _INDEX_SIZE * n
        bd.Usage          = _USAGE_DYNAMIC
        bd.BindFlags      = _BIND_INDEX_BUFFER
        bd.CPUAccessFlags = 0x10000
        ib = c_void_p()
        hr = _dev(self._dev, "CreateBuffer", c_int,
                  [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                  byref(bd), None, byref(ib))
        dx._check(hr, "imgui CreateBuffer (IB)")
        self._ib     = ib.value
        self._ib_cap = n

    def _map_write(self, buf: int, data: bytes):
        mapped = _MappedSubresource()
        hr = _ctx(self._ctx, "Map", c_int,
                  [c_void_p, c_uint, c_uint, c_uint, POINTER(_MappedSubresource)],
                  c_void_p(buf), 0, _MAP_WRITE_DISCARD, 0, byref(mapped))
        dx._check(hr, "imgui Map")
        ctypes.memmove(mapped.pData, data, len(data))
        _ctx(self._ctx, "Unmap", None, [c_void_p, c_uint], c_void_p(buf), 0)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        for attr in ("_vs", "_ps", "_il", "_cb", "_vb", "_ib",
                     "_blend", "_rast", "_ds", "_sampler"):
            v = getattr(self, attr, 0)
            if v:
                dx._release(v)
                setattr(self, attr, 0)
        for srv in self._srv_table:
            if srv:
                dx._release(srv)
        self._srv_table = [0]
        self._uid_to_idx.clear()
        self._font_srv = 0
