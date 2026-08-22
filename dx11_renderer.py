"""
dx11_renderer.py — High-level DX11 2D geometry and text renderer.

Builds on dx11_bridge.py.  Provides:
  * Renderer   — manages shaders, blend state, constant buffer, vertex buffer
  * draw_line  — single anti-aliased line segment (via two triangles)
  * draw_circle — circle as line-loop
  * draw_circle_filled — filled circle as triangle fan
  * draw_rect  — hollow rectangle
  * draw_text  — renders a text string via GDI → staging texture → SRV

All colours are (r, g, b, a) floats 0-1, PRE-MULTIPLIED for the overlay swap chain.

Usage:
    r = Renderer(device, context, width, height)
    # each frame:
    r.begin(rtv)
    r.draw_line(x0, y0, x1, y1, thickness, col)
    r.draw_circle(cx, cy, radius, col, segments=64)
    r.end()
    # on resize:
    r.resize(new_w, new_h)
    # on shutdown:
    r.release()
"""
import ctypes
from ctypes import (
    c_int, c_uint, c_float, c_void_p, c_char_p,
    POINTER, byref, Structure, c_ubyte, c_ulong, c_ushort, c_size_t,
    c_wchar_p,
)
import math
import struct

import dx11_bridge as dx


# ---------------------------------------------------------------------------
# D3DCompile
# ---------------------------------------------------------------------------

_d3dcompiler = ctypes.windll.LoadLibrary("d3dcompiler_47.dll")
_D3DCompile  = _d3dcompiler.D3DCompile
_D3DCompile.restype  = c_int
_D3DCompile.argtypes = [
    c_void_p,      # pSrcData
    c_size_t,      # SrcDataSize
    c_char_p,      # pSourceName
    c_void_p,      # pDefines
    c_void_p,      # pInclude
    c_char_p,      # pEntrypoint
    c_char_p,      # pTarget
    c_uint,        # Flags1
    c_uint,        # Flags2
    POINTER(c_void_p),  # ppCode (ID3DBlob*)
    POINTER(c_void_p),  # ppErrorMsgs (ID3DBlob*)
]

_d3d11 = ctypes.windll.d3d11


def _blob_data(blob: int) -> bytes:
    """Return bytes from an ID3DBlob, then Release it.
    ID3DBlob vtable: IUnknown(0-2), GetBufferPointer=3, GetBufferSize=4.
    """
    ptr  = dx._com(blob, 3, c_void_p, [])   # GetBufferPointer
    size = dx._com(blob, 4, c_size_t, [])   # GetBufferSize
    data = bytes((c_ubyte * size).from_address(ptr))
    dx._release(blob)
    return data


def _compile_shader(src: str, entry: str, target: str) -> bytes:
    src_b = src.encode("utf-8")
    code_blob  = c_void_p(None)
    err_blob   = c_void_p(None)
    hr = _D3DCompile(
        src_b, len(src_b), None, None, None,
        entry.encode(), target.encode(),
        0, 0,
        byref(code_blob), byref(err_blob),
    )
    if hr < 0:
        msg = ""
        if err_blob.value:
            msg = _blob_data(err_blob.value).decode(errors="replace")
        raise RuntimeError(f"Shader compile failed ({target}/{entry}): {msg}")
    if err_blob.value:
        dx._release(err_blob.value)
    return _blob_data(code_blob.value)


# ---------------------------------------------------------------------------
# Shaders — colour-only (geometry) and textured (text)
# ---------------------------------------------------------------------------

_GEOM_VS_SRC = r"""
cbuffer CB : register(b0) {
    float2 invScreenSize;   // 1/w, 1/h
};
struct VSIn  { float2 pos : POSITION; float4 col : COLOR; };
struct VSOut { float4 pos : SV_POSITION; float4 col : COLOR; };
VSOut main(VSIn v) {
    VSOut o;
    // Map pixel coords [0..w, 0..h] → NDC [-1..1, 1..-1]
    o.pos = float4(v.pos.x * invScreenSize.x * 2.0 - 1.0,
                   1.0 - v.pos.y * invScreenSize.y * 2.0,
                   0.0, 1.0);
    o.col = v.col;
    return o;
}
"""

_GEOM_PS_SRC = r"""
struct PSIn { float4 pos : SV_POSITION; float4 col : COLOR; };
float4 main(PSIn p) : SV_TARGET {
    // Premultiply alpha before writing to the DComp premultiplied-alpha swap chain.
    return float4(p.col.rgb * p.col.a, p.col.a);
}
"""

_TEXT_VS_SRC = r"""
cbuffer CB : register(b0) {
    float2 invScreenSize;
};
struct VSIn  { float2 pos : POSITION; float2 uv : TEXCOORD; };
struct VSOut { float4 pos : SV_POSITION; float2 uv : TEXCOORD; };
VSOut main(VSIn v) {
    VSOut o;
    o.pos = float4(v.pos.x * invScreenSize.x * 2.0 - 1.0,
                   1.0 - v.pos.y * invScreenSize.y * 2.0,
                   0.0, 1.0);
    o.uv  = v.uv;
    return o;
}
"""

_TEXT_PS_SRC = r"""
Texture2D    tex : register(t0);
SamplerState sam : register(s0);
cbuffer CB2 : register(b1) {
    float4 textColor;
};
struct PSIn { float4 pos : SV_POSITION; float2 uv : TEXCOORD; };
float4 main(PSIn p) : SV_TARGET {
    float alpha = tex.Sample(sam, p.uv).r;   // grayscale glyph
    float4 c = textColor * alpha;             // premultiply
    return c;
}
"""


# ---------------------------------------------------------------------------
# D3D11 structures we need beyond dx11_bridge
# ---------------------------------------------------------------------------

class _InputElement(Structure):
    _fields_ = [
        ("SemanticName",      c_char_p),
        ("SemanticIndex",     c_uint),
        ("Format",            c_uint),
        ("InputSlot",         c_uint),
        ("AlignedByteOffset", c_uint),
        ("InputSlotClass",    c_uint),
        ("InstanceDataStepRate", c_uint),
    ]


# DXGI_FORMAT constants
_FMT_R32G32_FLOAT       = 16
_FMT_R32G32B32A32_FLOAT = 2
_FMT_R8_UNORM           = 61

# D3D11_INPUT_CLASSIFICATION
_INPUT_PER_VERTEX = 0

# D3D11_BIND
_BIND_VERTEX_BUFFER    = 0x1
_BIND_CONSTANT_BUFFER  = 0x4
_BIND_SHADER_RESOURCE  = 0x8

# D3D11_USAGE
_USAGE_DEFAULT  = 0
_USAGE_DYNAMIC  = 2

# D3D11_CPU_ACCESS
_CPU_WRITE = 0x10000

# D3D11_MAP
_MAP_WRITE_DISCARD = 4

# D3D11_PRIMITIVE_TOPOLOGY
_PRIM_TRIANGLELIST  = 4

# D3D11_BLEND / D3D11_BLEND_OP
_BLEND_ONE           = 2
_BLEND_SRC_ALPHA     = 5
_BLEND_INV_SRC_ALPHA = 6
_BLEND_OP_ADD        = 1


class _BlendDesc(Structure):
    """Simplified D3D11_BLEND_DESC — one render target, rest zero."""
    class _RT(Structure):
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


class _RastDesc(Structure):
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


class _BufDesc(Structure):
    _fields_ = [
        ("ByteWidth",           c_uint),
        ("Usage",               c_uint),
        ("BindFlags",           c_uint),
        ("CPUAccessFlags",      c_uint),
        ("MiscFlags",           c_uint),
        ("StructureByteStride", c_uint),
    ]


class _SubresData(Structure):
    _fields_ = [
        ("pSysMem",          c_void_p),
        ("SysMemPitch",      c_uint),
        ("SysMemSlicePitch", c_uint),
    ]


class _Tex2DDesc(Structure):
    _fields_ = [
        ("Width",              c_uint),
        ("Height",             c_uint),
        ("MipLevels",          c_uint),
        ("ArraySize",          c_uint),
        ("Format",             c_uint),
        ("SampleDesc",         dx._SampleDesc),
        ("Usage",              c_uint),
        ("BindFlags",          c_uint),
        ("CPUAccessFlags",     c_uint),
        ("MiscFlags",          c_uint),
    ]


class _SamplerDesc(Structure):
    _fields_ = [
        ("Filter",         c_uint),   # D3D11_FILTER_MIN_MAG_MIP_LINEAR = 21
        ("AddressU",       c_uint),   # WRAP=1 CLAMP=3
        ("AddressV",       c_uint),
        ("AddressW",       c_uint),
        ("MipLODBias",     c_float),
        ("MaxAnisotropy",  c_uint),
        ("ComparisonFunc", c_uint),
        ("BorderColor",    c_float * 4),
        ("MinLOD",         c_float),
        ("MaxLOD",         c_float),
    ]


class _MappedSubresource(Structure):
    _fields_ = [
        ("pData",       c_void_p),
        ("RowPitch",    c_uint),
        ("DepthPitch",  c_uint),
    ]


# ---------------------------------------------------------------------------
# Vertex structs (byte layouts)
# ---------------------------------------------------------------------------
# Geom vertex:  float2 pos + float4 col  = 24 bytes
# Text vertex:  float2 pos + float2 uv   = 16 bytes

_GEOM_STRIDE = 24
_TEXT_STRIDE = 16
_MAX_VERTS   = 65536    # vertex buffer capacity


def _pack_geom_verts(*verts) -> bytes:
    """verts: sequence of (x, y, r, g, b, a) tuples."""
    return b"".join(struct.pack("6f", *v) for v in verts)


def _pack_text_verts(*verts) -> bytes:
    """verts: sequence of (x, y, u, v) tuples."""
    return b"".join(struct.pack("4f", *v) for v in verts)


# ---------------------------------------------------------------------------
# Device slot indices (D3D11 immediate context vtable)
# Actual offsets from Windows SDK d3d11.h (counted manually from interface def)
# ID3D11DeviceContext inherits ID3D11DeviceChild (4 methods) + IUnknown (3) = 7 base
# Then its own methods in declaration order:
_CTX_SLOTS = {
    # IUnknown
    "QueryInterface": 0, "AddRef": 1, "Release": 2,
    # ID3D11DeviceChild
    "GetDevice": 3, "GetPrivateData": 4, "SetPrivateData": 5, "SetPrivateDataInterface": 6,
    # ID3D11DeviceContext — in header order
    "VSSetConstantBuffers":         7,
    "PSSetShaderResources":         8,
    "PSSetShader":                  9,
    "PSSetSamplers":               10,
    "VSSetShader":                 11,
    "DrawIndexed":                 12,
    "Draw":                        13,
    "Map":                         14,
    "Unmap":                       15,
    "PSSetConstantBuffers":        16,
    "IASetInputLayout":            17,
    "IASetVertexBuffers":          18,
    "IASetIndexBuffer":            19,
    "DrawIndexedInstanced":        20,
    "DrawInstanced":               21,
    "GSSetConstantBuffers":        22,
    "GSSetShader":                 23,
    "IASetPrimitiveTopology":      24,
    "VSSetShaderResources":        25,
    "VSSetSamplers":               26,
    "Begin":                       27,
    "End":                         28,
    "GetData":                     29,
    "SetPredication":              30,
    "GSSetShaderResources":        31,
    "GSSetSamplers":               32,
    "OMSetRenderTargets":          33,
    "OMSetRenderTargetsAndUnorderedAccessViews": 34,
    "OMSetBlendState":             35,
    "OMSetDepthStencilState":      36,
    "SOSetTargets":                37,
    "DrawAuto":                    38,
    "DrawIndexedInstancedIndirect":39,
    "DrawInstancedIndirect":       40,
    "Dispatch":                    41,
    "DispatchIndirect":            42,
    "RSSetState":                  43,
    "RSSetViewports":              44,
    "RSSetScissorRects":           45,
    "CopySubresourceRegion":       46,
    "CopyResource":                47,
    "UpdateSubresource":           48,
    "CopyStructureCount":          49,
    "ClearRenderTargetView":       50,
    "ClearUnorderedAccessViewUint":51,
    "ClearUnorderedAccessViewFloat":52,
    "ClearDepthStencilView":       53,
    "GenerateMips":                54,
    "SetResourceMinLOD":           55,
    "GetResourceMinLOD":           56,
    "ResolveSubresource":          57,
    "ExecuteCommandList":          58,
    "HSSetShaderResources":        59,
    "HSSetShader":                 60,
    "HSSetSamplers":               61,
    "HSSetConstantBuffers":        62,
    "DSSetShaderResources":        63,
    "DSSetShader":                 64,
    "DSSetSamplers":               65,
    "DSSetConstantBuffers":        66,
    "CSSetShaderResources":        67,
    "CSSetUnorderedAccessViews":   68,
    "CSSetShader":                 69,
    "CSSetSamplers":               70,
    "CSSetConstantBuffers":        71,
    "VSGetConstantBuffers":        72,
    "PSGetShaderResources":        73,
    "PSGetShader":                 74,
    "PSGetSamplers":               75,
    "VSGetShader":                 76,
    "PSGetConstantBuffers":        77,
    "IAGetInputLayout":            78,
    "IAGetVertexBuffers":          79,
    "IAGetIndexBuffer":            80,
    "GSGetConstantBuffers":        81,
    "GSGetShader":                 82,
    "IAGetPrimitiveTopology":      83,
    "VSGetShaderResources":        84,
    "VSGetSamplers":               85,
    "GetPredication":              86,
    "GSGetShaderResources":        87,
    "GSGetSamplers":               88,
    "OMGetRenderTargets":          89,
    "OMGetRenderTargetsAndUnorderedAccessViews": 90,
    "OMGetBlendState":             91,
    "OMGetDepthStencilState":      92,
    "SOGetTargets":                93,
    "RSGetState":                  94,
    "RSGetViewports":              95,
    "RSGetScissorRects":           96,
    "HSGetShaderResources":        97,
    "HSGetShader":                 98,
    "HSGetSamplers":               99,
    "HSGetConstantBuffers":       100,
    "DSGetShaderResources":       101,
    "DSGetShader":                102,
    "DSGetSamplers":              103,
    "DSGetConstantBuffers":       104,
    "CSGetShaderResources":       105,
    "CSGetUnorderedAccessViews":  106,
    "CSGetShader":                107,
    "CSGetSamplers":              108,
    "CSGetConstantBuffers":       109,
    "ClearState":                 110,
    "Flush":                      111,
    "GetType":                    112,
    "GetContextFlags":            113,
    "FinishCommandList":          114,
}

# ID3D11Device vtable slots (IUnknown: 0-2, then own methods in header order)
# 3=CreateBuffer, 4=CreateTexture1D, 5=CreateTexture2D, 6=CreateTexture3D,
# 7=CreateShaderResourceView, 8=CreateUnorderedAccessView, 9=CreateRenderTargetView,
# 10=CreateDepthStencilView, 11=CreateInputLayout, 12=CreateVertexShader,
# 13=CreateGeometryShader, 14=CreateGeometryShaderWithStreamOutput, 15=CreatePixelShader,
# 16=CreateHullShader, 17=CreateDomainShader, 18=CreateComputeShader,
# 19=CreateClassLinkage, 20=CreateBlendState, 21=CreateDepthStencilState,
# 22=CreateRasterizerState, 23=CreateSamplerState
_DEV_SLOTS = {
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
}


def _ctx(ctx: int, name: str, res_type, arg_types: list, *args):
    return dx._com(ctx, _CTX_SLOTS[name], res_type, arg_types, *args)


def _dev(dev: int, name: str, res_type, arg_types: list, *args):
    return dx._com(dev, _DEV_SLOTS[name], res_type, arg_types, *args)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class Renderer:
    """
    DX11 2D geometry + text renderer.
    Thread-safety: call all draw_* methods from the same thread that created it.
    """

    def __init__(self, device: int, context: int, width: int, height: int):
        self._dev = device
        self._ctx = context
        self._w   = width
        self._h   = height

        self._geom_vs = self._geom_ps = 0
        self._text_vs = self._text_ps = 0
        self._geom_il = self._text_il = 0
        self._geom_vb = self._text_vb = 0
        self._cb0     = 0     # invScreenSize constant buffer (shared VS)
        self._cb1     = 0     # textColor constant buffer (text PS)
        self._blend   = 0
        self._rast    = 0
        self._sampler = 0

        self._pending_geom: list[bytes] = []  # packed vertex data chunks
        self._pending_text: list        = []  # (vb_bytes, srv, col4)

        self._build_shaders()
        self._build_states()
        self._build_buffers()

    # ------------------------------------------------------------------
    # Internal build helpers
    # ------------------------------------------------------------------

    def _build_shaders(self):
        dev = self._dev

        # Geometry shaders
        gvs_bc = _compile_shader(_GEOM_VS_SRC, "main", "vs_4_0")
        gps_bc = _compile_shader(_GEOM_PS_SRC, "main", "ps_4_0")
        tvs_bc = _compile_shader(_TEXT_VS_SRC, "main", "vs_4_0")
        tps_bc = _compile_shader(_TEXT_PS_SRC, "main", "ps_4_0")

        def make_vs(bc):
            vs = c_void_p(None)
            hr = _dev(dev, "CreateVertexShader", c_int,
                      [c_void_p, c_size_t, c_void_p, POINTER(c_void_p)],
                      bc, len(bc), None, byref(vs))
            dx._check(hr, "CreateVertexShader")
            return vs.value

        def make_ps(bc):
            ps = c_void_p(None)
            hr = _dev(dev, "CreatePixelShader", c_int,
                      [c_void_p, c_size_t, c_void_p, POINTER(c_void_p)],
                      bc, len(bc), None, byref(ps))
            dx._check(hr, "CreatePixelShader")
            return ps.value

        self._geom_vs = make_vs(gvs_bc)
        self._geom_ps = make_ps(gps_bc)
        self._text_vs = make_vs(tvs_bc)
        self._text_ps = make_ps(tps_bc)

        # Input layouts
        # Geometry: POSITION float2 + COLOR float4
        geom_elems = (_InputElement * 2)(
            _InputElement(b"POSITION", 0, _FMT_R32G32_FLOAT, 0, 0,         _INPUT_PER_VERTEX, 0),
            _InputElement(b"COLOR",    0, _FMT_R32G32B32A32_FLOAT, 0, 8,   _INPUT_PER_VERTEX, 0),
        )
        il = c_void_p(None)
        hr = _dev(dev, "CreateInputLayout", c_int,
                  [POINTER(_InputElement * 2), c_uint, c_void_p, c_size_t, POINTER(c_void_p)],
                  geom_elems, 2, gvs_bc, len(gvs_bc), byref(il))
        dx._check(hr, "CreateInputLayout (geom)")
        self._geom_il = il.value

        # Text: POSITION float2 + TEXCOORD float2
        _FMT_UV = _FMT_R32G32_FLOAT
        text_elems = (_InputElement * 2)(
            _InputElement(b"POSITION", 0, _FMT_R32G32_FLOAT, 0, 0, _INPUT_PER_VERTEX, 0),
            _InputElement(b"TEXCOORD", 0, _FMT_UV,           0, 8, _INPUT_PER_VERTEX, 0),
        )
        il2 = c_void_p(None)
        hr  = _dev(dev, "CreateInputLayout", c_int,
                   [POINTER(_InputElement * 2), c_uint, c_void_p, c_size_t, POINTER(c_void_p)],
                   text_elems, 2, tvs_bc, len(tvs_bc), byref(il2))
        dx._check(hr, "CreateInputLayout (text)")
        self._text_il = il2.value

    def _build_states(self):
        dev = self._dev

        # Blend: premultiplied alpha — geometry PS already premultiplies,
        # text PS outputs textColor*alpha (also premultiplied).
        # src=ONE preserves the premultiplied value; dst=INV_SRC_ALPHA composites correctly.
        bd = _BlendDesc()
        bd.AlphaToCoverageEnable  = 0
        bd.IndependentBlendEnable = 0
        rt = bd.RenderTarget[0]
        rt.BlendEnable    = 1
        rt.SrcBlend       = _BLEND_ONE
        rt.DestBlend      = _BLEND_INV_SRC_ALPHA
        rt.BlendOp        = _BLEND_OP_ADD
        rt.SrcBlendAlpha  = _BLEND_ONE
        rt.DestBlendAlpha = _BLEND_INV_SRC_ALPHA
        rt.BlendOpAlpha   = _BLEND_OP_ADD
        rt.RenderTargetWriteMask = 0x0F
        bs = c_void_p(None)
        hr = _dev(dev, "CreateBlendState", c_int,
                  [POINTER(_BlendDesc), POINTER(c_void_p)],
                  byref(bd), byref(bs))
        dx._check(hr, "CreateBlendState")
        self._blend = bs.value

        # Rasterizer: solid, no cull, no depth clip
        rd = _RastDesc()
        rd.FillMode              = 3   # D3D11_FILL_SOLID
        rd.CullMode              = 1   # D3D11_CULL_NONE
        rd.FrontCounterClockwise = 0
        rd.DepthBias             = 0
        rd.DepthBiasClamp        = 0.0
        rd.SlopeScaledDepthBias  = 0.0
        rd.DepthClipEnable       = 0
        rd.ScissorEnable         = 0
        rd.MultisampleEnable     = 0
        rd.AntialiasedLineEnable = 0
        rs = c_void_p(None)
        hr = _dev(dev, "CreateRasterizerState", c_int,
                  [POINTER(_RastDesc), POINTER(c_void_p)],
                  byref(rd), byref(rs))
        dx._check(hr, "CreateRasterizerState")
        self._rast = rs.value

        # Sampler: linear, clamp
        sd = _SamplerDesc()
        sd.Filter         = 21    # D3D11_FILTER_MIN_MAG_MIP_LINEAR
        sd.AddressU       = 3     # CLAMP
        sd.AddressV       = 3
        sd.AddressW       = 3
        sd.MipLODBias     = 0.0
        sd.MaxAnisotropy  = 1
        sd.ComparisonFunc = 1     # NEVER
        sd.MinLOD         = -3.402823466e+38
        sd.MaxLOD         =  3.402823466e+38
        smp = c_void_p(None)
        hr  = _dev(dev, "CreateSamplerState", c_int,
                   [POINTER(_SamplerDesc), POINTER(c_void_p)],
                   byref(sd), byref(smp))
        dx._check(hr, "CreateSamplerState")
        self._sampler = smp.value

    def _build_buffers(self):
        dev = self._dev

        def make_vb(stride, max_verts):
            bd = _BufDesc()
            bd.ByteWidth      = stride * max_verts
            bd.Usage          = _USAGE_DYNAMIC
            bd.BindFlags      = _BIND_VERTEX_BUFFER
            bd.CPUAccessFlags = _CPU_WRITE
            vb = c_void_p(None)
            hr = _dev(dev, "CreateBuffer", c_int,
                      [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                      byref(bd), None, byref(vb))
            dx._check(hr, "CreateBuffer (VB)")
            return vb.value

        self._geom_vb = make_vb(_GEOM_STRIDE, _MAX_VERTS)
        self._text_vb = make_vb(_TEXT_STRIDE, 6)   # one glyph quad at a time

        # Constant buffer 0: float2 invScreenSize (padded to 16 bytes)
        cbd = _BufDesc()
        cbd.ByteWidth      = 16   # min 16-byte alignment
        cbd.Usage          = _USAGE_DYNAMIC
        cbd.BindFlags      = _BIND_CONSTANT_BUFFER
        cbd.CPUAccessFlags = _CPU_WRITE
        cb0 = c_void_p(None)
        hr  = _dev(dev, "CreateBuffer", c_int,
                   [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                   byref(cbd), None, byref(cb0))
        dx._check(hr, "CreateBuffer (CB0)")
        self._cb0 = cb0.value

        # Constant buffer 1: float4 textColor
        cb1 = c_void_p(None)
        hr  = _dev(dev, "CreateBuffer", c_int,
                   [POINTER(_BufDesc), c_void_p, POINTER(c_void_p)],
                   byref(cbd), None, byref(cb1))
        dx._check(hr, "CreateBuffer (CB1)")
        self._cb1 = cb1.value

        self._update_cb0()

    def _update_cb0(self):
        data = struct.pack("4f", 1.0 / self._w, 1.0 / self._h, 0.0, 0.0)
        self._map_write(self._cb0, data)

    def _map_write(self, buf: int, data: bytes):
        ctx = self._ctx
        mapped = _MappedSubresource()
        hr = _ctx(ctx, "Map", c_int,
                  [c_void_p, c_uint, c_uint, c_uint, POINTER(_MappedSubresource)],
                  c_void_p(buf), 0, _MAP_WRITE_DISCARD, 0, byref(mapped))
        dx._check(hr, "Map")
        ctypes.memmove(mapped.pData, data, len(data))
        _ctx(ctx, "Unmap", None, [c_void_p, c_uint], c_void_p(buf), 0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resize(self, width: int, height: int):
        self._w = width
        self._h = height
        self._update_cb0()

    def begin(self):
        """Set up render state.  Call once per frame before draw_* methods."""
        ctx = self._ctx
        # viewport already set by OverlayApp after resize; no-op here
        _ctx(ctx, "OMSetBlendState", None,
             [c_void_p, c_void_p, c_uint],
             c_void_p(self._blend), None, 0xFFFFFFFF)
        _ctx(ctx, "RSSetState", None, [c_void_p], c_void_p(self._rast))
        # VS constant buffer 0
        cb_arr = (c_void_p * 1)(self._cb0)
        _ctx(ctx, "VSSetConstantBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1)],
             0, 1, cb_arr)
        self._pending_geom = []
        self._pending_text = []

    def end(self):
        """Flush all batched geometry to GPU."""
        self._flush_geom()
        self._flush_text()

    # ------------------------------------------------------------------
    # Geometry draw calls (batched)
    # ------------------------------------------------------------------

    def draw_line(self, x0: float, y0: float, x1: float, y1: float,
                  thickness: float, col: tuple):
        """Draw a line segment as a rectangle (two triangles)."""
        r, g, b, a = col
        # Direction perpendicular to the line
        dx_ = x1 - x0
        dy_ = y1 - y0
        length = math.hypot(dx_, dy_)
        if length < 0.001:
            return
        nx = -dy_ / length * (thickness * 0.5)
        ny =  dx_ / length * (thickness * 0.5)
        # Four corners
        ax, ay = x0 + nx, y0 + ny
        bx, by = x0 - nx, y0 - ny
        cx_, cy = x1 + nx, y1 + ny
        dx2, dy2 = x1 - nx, y1 - ny
        # Two triangles (CW winding)
        verts = _pack_geom_verts(
            (ax, ay, r, g, b, a), (cx_, cy, r, g, b, a), (bx, by, r, g, b, a),
            (bx, by, r, g, b, a), (cx_, cy, r, g, b, a), (dx2, dy2, r, g, b, a),
        )
        self._pending_geom.append(verts)

    def draw_rect(self, x: float, y: float, w: float, h: float,
                  thickness: float, col: tuple):
        """Hollow axis-aligned rectangle."""
        self.draw_line(x,     y,     x + w, y,     thickness, col)
        self.draw_line(x + w, y,     x + w, y + h, thickness, col)
        self.draw_line(x + w, y + h, x,     y + h, thickness, col)
        self.draw_line(x,     y + h, x,     y,     thickness, col)

    def draw_circle(self, cx: float, cy: float, radius: float,
                    col: tuple, thickness: float = 1.5, segments: int = 48):
        """Hollow circle."""
        step = 2.0 * math.pi / segments
        for i in range(segments):
            a0 = i * step
            a1 = (i + 1) * step
            x0 = cx + math.cos(a0) * radius
            y0 = cy + math.sin(a0) * radius
            x1 = cx + math.cos(a1) * radius
            y1 = cy + math.sin(a1) * radius
            self.draw_line(x0, y0, x1, y1, thickness, col)

    def draw_arc(self, cx: float, cy: float, radius: float,
                start_deg: float, end_deg: float, col: tuple,
                thickness: float = 1.5, segments: int = 24):
        """Hollow circular arc from start_deg to end_deg (degrees, measured
        clockwise starting straight up — 0 deg = 12 o'clock — regardless of
        the underlying y-down screen coordinate system). Used for icon-style
        rings with a gap (e.g. the running-indicator power-button badge)."""
        span = end_deg - start_deg
        if span <= 0:
            return
        n = max(1, int(round(segments * span / 360.0)))
        step = math.radians(span) / n
        start_rad = math.radians(start_deg)
        for i in range(n):
            a0 = start_rad + i * step
            a1 = start_rad + (i + 1) * step
            x0 = cx + math.sin(a0) * radius
            y0 = cy - math.cos(a0) * radius
            x1 = cx + math.sin(a1) * radius
            y1 = cy - math.cos(a1) * radius
            self.draw_line(x0, y0, x1, y1, thickness, col)

    def draw_circle_filled(self, cx: float, cy: float, radius: float,
                           col: tuple, segments: int = 48):
        """Filled circle as triangle fan."""
        r, g, b, a = col
        step  = 2.0 * math.pi / segments
        verts = []
        for i in range(segments):
            a0 = i * step
            a1 = (i + 1) * step
            verts.append((cx, cy, r, g, b, a))
            verts.append((cx + math.cos(a0) * radius,
                          cy + math.sin(a0) * radius, r, g, b, a))
            verts.append((cx + math.cos(a1) * radius,
                          cy + math.sin(a1) * radius, r, g, b, a))
        self._pending_geom.append(_pack_geom_verts(*verts))

    # ------------------------------------------------------------------
    # Text rendering
    # ------------------------------------------------------------------

    def measure_text(self, text: str, font_size: int = 14,
                     font_face: str = "Consolas") -> tuple[int, int]:
        """Measure (width, height) in pixels without drawing — use to center
        short labels before calling draw_text()."""
        if not text:
            return 0, 0
        try:
            return _gdi_measure_text(text, font_size, font_face)
        except Exception as exc:
            print(f"[Renderer] measure_text failed for {text!r}: {exc}")
            return 0, 0

    def draw_text(self, text: str, x: float, y: float, col: tuple,
                  font_size: int = 14, font_face: str = "Consolas"):
        """
        Render *text* using GDI into a one-channel texture, then draw it.
        col is (r,g,b,a) pre-multiplied floats.
        Returns the pixel width of the rendered text.
        """
        if not text:
            return 0
        try:
            srv, tw, th = _gdi_text_to_srv(self._dev, text, font_size, font_face)
        except Exception as exc:
            print(f"[Renderer] draw_text failed for {text!r}: {exc}")
            return 0
        # Build a quad in _pending_text
        x1, y1 = x + tw, y + th
        verts = _pack_text_verts(
            (x,  y,  0.0, 0.0),
            (x1, y,  1.0, 0.0),
            (x,  y1, 0.0, 1.0),
            (x,  y1, 0.0, 1.0),
            (x1, y,  1.0, 0.0),
            (x1, y1, 1.0, 1.0),
        )
        self._pending_text.append((verts, srv, col))
        return tw

    # ------------------------------------------------------------------
    # Flush helpers (called by end())
    # ------------------------------------------------------------------

    def _flush_geom(self):
        if not self._pending_geom:
            return
        ctx = self._ctx
        data = b"".join(self._pending_geom)
        n_verts = len(data) // _GEOM_STRIDE
        if n_verts == 0:
            return

        # Upload to vertex buffer
        self._map_write(self._geom_vb, data[:_GEOM_STRIDE * min(n_verts, _MAX_VERTS)])

        # Set pipeline
        _ctx(ctx, "IASetInputLayout", None, [c_void_p], c_void_p(self._geom_il))
        stride = c_uint(_GEOM_STRIDE)
        offset = c_uint(0)
        vb_arr = (c_void_p * 1)(self._geom_vb)
        _ctx(ctx, "IASetVertexBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1), POINTER(c_uint), POINTER(c_uint)],
             0, 1, vb_arr, byref(stride), byref(offset))
        _ctx(ctx, "IASetPrimitiveTopology", None, [c_uint], _PRIM_TRIANGLELIST)
        _ctx(ctx, "VSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._geom_vs), None, 0)
        _ctx(ctx, "PSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._geom_ps), None, 0)
        _ctx(ctx, "Draw", None, [c_uint, c_uint], min(n_verts, _MAX_VERTS), 0)

    def _flush_text(self):
        if not self._pending_text:
            return
        ctx = self._ctx
        _ctx(ctx, "IASetInputLayout", None, [c_void_p], c_void_p(self._text_il))
        stride = c_uint(_TEXT_STRIDE)
        offset = c_uint(0)
        vb_arr = (c_void_p * 1)(self._text_vb)
        _ctx(ctx, "IASetVertexBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1), POINTER(c_uint), POINTER(c_uint)],
             0, 1, vb_arr, byref(stride), byref(offset))
        _ctx(ctx, "IASetPrimitiveTopology", None, [c_uint], _PRIM_TRIANGLELIST)
        _ctx(ctx, "VSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._text_vs), None, 0)
        _ctx(ctx, "PSSetShader", None, [c_void_p, c_void_p, c_uint],
             c_void_p(self._text_ps), None, 0)
        smp_arr = (c_void_p * 1)(self._sampler)
        _ctx(ctx, "PSSetSamplers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1)],
             0, 1, smp_arr)
        cb_arr1 = (c_void_p * 1)(self._cb1)
        _ctx(ctx, "PSSetConstantBuffers", None,
             [c_uint, c_uint, POINTER(c_void_p * 1)],
             1, 1, cb_arr1)

        for verts, srv, col in self._pending_text:
            # Update color CB
            self._map_write(self._cb1, struct.pack("4f", *col))
            # Upload quad
            self._map_write(self._text_vb, verts)
            # Bind SRV
            srv_arr = (c_void_p * 1)(srv)
            _ctx(ctx, "PSSetShaderResources", None,
                 [c_uint, c_uint, POINTER(c_void_p * 1)],
                 0, 1, srv_arr)
            _ctx(ctx, "Draw", None, [c_uint, c_uint], 6, 0)
            # Release per-frame SRV (texture was created just for this frame)
            dx._release(srv)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def release(self):
        for attr in ("_geom_vs", "_geom_ps", "_text_vs", "_text_ps",
                     "_geom_il", "_text_il",
                     "_geom_vb", "_text_vb",
                     "_cb0", "_cb1",
                     "_blend", "_rast", "_sampler"):
            v = getattr(self, attr, 0)
            if v:
                dx._release(v)
                setattr(self, attr, 0)


# ---------------------------------------------------------------------------
# GDI text → D3D11 R8_UNORM texture SRV
# ---------------------------------------------------------------------------

_gdi32  = ctypes.windll.gdi32
_user32 = ctypes.windll.user32

_CreateCompatibleDC = _gdi32.CreateCompatibleDC
_CreateCompatibleDC.restype  = c_void_p
_CreateCompatibleDC.argtypes = [c_void_p]

_DeleteDC = _gdi32.DeleteDC
_DeleteDC.restype  = c_int
_DeleteDC.argtypes = [c_void_p]

_CreateDIBSection = _gdi32.CreateDIBSection
_CreateDIBSection.restype  = c_void_p
_CreateDIBSection.argtypes = [c_void_p, c_void_p, c_uint,
                               POINTER(c_void_p), c_void_p, c_uint]

_DeleteObject = _gdi32.DeleteObject
_DeleteObject.restype  = c_int
_DeleteObject.argtypes = [c_void_p]

_SelectObject = _gdi32.SelectObject
_SelectObject.restype  = c_void_p
_SelectObject.argtypes = [c_void_p, c_void_p]

_SetBkMode = _gdi32.SetBkMode
_SetBkMode.restype  = c_int
_SetBkMode.argtypes = [c_void_p, c_int]

_SetTextColor = _gdi32.SetTextColor
_SetTextColor.restype  = c_ulong
_SetTextColor.argtypes = [c_void_p, c_ulong]

_TextOutW = _gdi32.TextOutW
_TextOutW.restype  = c_int
_TextOutW.argtypes = [c_void_p, c_int, c_int, c_wchar_p, c_int]

_GetTextExtentPoint32W = _gdi32.GetTextExtentPoint32W
_GetTextExtentPoint32W.restype  = c_int
_GetTextExtentPoint32W.argtypes = [c_void_p, c_wchar_p, c_int, c_void_p]

_CreateFontW = _gdi32.CreateFontW
_CreateFontW.restype  = c_void_p
_CreateFontW.argtypes = [c_int, c_int, c_int, c_int, c_int,
                          c_uint, c_uint, c_uint, c_uint, c_uint,
                          c_uint, c_uint, c_uint, c_wchar_p]

_BitBlt = _gdi32.BitBlt
_BitBlt.restype  = c_int
_BitBlt.argtypes = [c_void_p, c_int, c_int, c_int, c_int,
                    c_void_p, c_int, c_int, c_uint]


class _SIZE(Structure):
    _fields_ = [("cx", c_int), ("cy", c_int)]


class _BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize",          c_uint),
        ("biWidth",         c_int),
        ("biHeight",        c_int),
        ("biPlanes",        c_ushort),
        ("biBitCount",      c_ushort),
        ("biCompression",   c_uint),
        ("biSizeImage",     c_uint),
        ("biXPelsPerMeter", c_int),
        ("biYPelsPerMeter", c_int),
        ("biClrUsed",       c_uint),
        ("biClrImportant",  c_uint),
    ]


class _BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", c_uint * 3)]


def _gdi_measure_text(text: str, font_size: int, font_face: str) -> tuple[int, int]:
    """Measure (width, height) in pixels for *text* at font_size/font_face
    without rasterizing it — used to pre-center short badge labels (e.g. the
    running-indicator's 'R9') before the real draw_text() call."""
    screen_dc = ctypes.windll.user32.GetDC(None)
    mem_dc    = _CreateCompatibleDC(screen_dc)
    hfont = _CreateFontW(
        -font_size, 0, 0, 0,
        400, 0, 0, 0, 0, 0, 0, 2, 0,
        font_face,
    )
    old_font = _SelectObject(mem_dc, hfont)
    sz = _SIZE()
    _GetTextExtentPoint32W(mem_dc, text, len(text), ctypes.addressof(sz))
    _SelectObject(mem_dc, old_font)
    _DeleteObject(hfont)
    _DeleteDC(mem_dc)
    ctypes.windll.user32.ReleaseDC(None, screen_dc)
    return max(sz.cx, 1), max(sz.cy, 1)


def _gdi_text_to_srv(device: int, text: str, font_size: int,
                     font_face: str) -> tuple[int, int, int]:
    """
    Render *text* with GDI into a grayscale bitmap, then upload it to a
    D3D11 R8_UNORM texture and return (SRV ptr, width, height).
    The caller must Release the SRV when done.
    """
    # --- measure text size ---
    screen_dc = ctypes.windll.user32.GetDC(None)
    mem_dc    = _CreateCompatibleDC(screen_dc)

    hfont = _CreateFontW(
        -font_size, 0, 0, 0,
        400,   # weight (normal)
        0, 0, 0,
        0,     # charset (ANSI)
        0, 0, 2,  # OUT_DEFAULT, CLIP_DEFAULT, ANTIALIASED_QUALITY
        0,
        font_face,
    )
    old_font = _SelectObject(mem_dc, hfont)

    sz = _SIZE()
    _GetTextExtentPoint32W(mem_dc, text, len(text), ctypes.addressof(sz))
    tw, th = max(sz.cx, 1), max(sz.cy, 1)

    # --- create DIB (32bpp BGRA, top-down) ---
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize     = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth    = tw
    bmi.bmiHeader.biHeight   = -th   # top-down
    bmi.bmiHeader.biPlanes   = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB

    bits_ptr = c_void_p(None)
    hbm = _CreateDIBSection(mem_dc, ctypes.addressof(bmi), 0, byref(bits_ptr), None, 0)
    if not hbm or not bits_ptr.value:
        _DeleteObject(hfont)
        _DeleteDC(mem_dc)
        ctypes.windll.user32.ReleaseDC(None, screen_dc)
        raise OSError(f"CreateDIBSection failed for text {text!r} ({tw}x{th})")
    _SelectObject(mem_dc, hbm)

    # --- draw text in white on black ---
    _SetBkMode(mem_dc, 1)      # TRANSPARENT
    _SetTextColor(mem_dc, 0x00FFFFFF)
    _TextOutW(mem_dc, 0, 0, text, len(text))

    # --- copy BGRA pixels into R8 array ---
    bgra = (c_ubyte * (tw * th * 4)).from_address(bits_ptr.value)
    r8   = (c_ubyte * (tw * th))()
    for i in range(tw * th):
        # white-on-black GDI: blue channel = luminance (all channels same)
        r8[i] = bgra[i * 4]   # B channel

    # --- cleanup GDI ---
    _SelectObject(mem_dc, old_font)
    _DeleteObject(hfont)
    _DeleteObject(hbm)
    _DeleteDC(mem_dc)
    ctypes.windll.user32.ReleaseDC(None, screen_dc)

    # --- upload to D3D11 R8_UNORM texture ---
    td = _Tex2DDesc()
    td.Width              = tw
    td.Height             = th
    td.MipLevels          = 1
    td.ArraySize          = 1
    td.Format             = _FMT_R8_UNORM
    td.SampleDesc.Count   = 1
    td.SampleDesc.Quality = 0
    td.Usage              = _USAGE_DEFAULT
    td.BindFlags          = _BIND_SHADER_RESOURCE
    td.CPUAccessFlags     = 0
    td.MiscFlags          = 0

    sd = _SubresData()
    sd.pSysMem          = ctypes.cast(r8, c_void_p)
    sd.SysMemPitch      = tw
    sd.SysMemSlicePitch = 0

    tex = c_void_p(None)
    hr  = _dev(device, "CreateTexture2D", c_int,
               [POINTER(_Tex2DDesc), POINTER(_SubresData), POINTER(c_void_p)],
               byref(td), byref(sd), byref(tex))
    dx._check(hr, "CreateTexture2D (text)")

    # CreateShaderResourceView with default desc (MipLevels=1)
    srv = c_void_p(None)
    hr  = _dev(device, "CreateShaderResourceView", c_int,
               [c_void_p, c_void_p, POINTER(c_void_p)],
               c_void_p(tex.value), None, byref(srv))
    dx._check(hr, "CreateShaderResourceView (text)")
    dx._release(tex.value)   # SRV holds a ref; release our ref

    return srv.value, tw, th
