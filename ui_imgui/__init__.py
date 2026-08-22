# ui_imgui — imgui panel implementations for R9Tools overlay
#
# *** FROZEN / EXPERIMENTAL — NOT SHIPPED — NOT MAINTAINED ***
# This package belongs to the alternate imgui + DX11 UI stack (entry point:
# main_imgui.py, plus imgui_overlay.py / imgui_backend.py at the repo root).
# It is NOT built or launched by anything currently shipping — the shipped
# app is main.py, using the PySide6 + DX11Overlay stack, which does not
# import from this package. It is kept only as a potential reference/fallback
# (see main_imgui.py's module docstring for why — it relates to an unverified
# FPS tradeoff around WS_EX_LAYERED click-through, discussed in dx11_overlay.py).
# This code has been allowed to drift and does NOT have feature parity with
# main.py as of this point in the project. Do not update it to chase parity;
# see main_imgui.py for the full explanation.
