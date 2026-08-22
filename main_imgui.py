"""
R9Tools — imgui + Direct3D 11 entry point.
Run as administrator (required for Interception driver).

*** FROZEN / EXPERIMENTAL — NOT SHIPPED — NOT MAINTAINED ***
This is an alternate UI implementation (this file, ui_imgui/, imgui_overlay.py,
imgui_backend.py) that is NOT built or launched by anything currently shipping.
The shipped app is main.py, using the PySide6 + DX11Overlay stack. main.py does
not import from this stack, and R9Tools.spec does not build from it.

It is intentionally kept (not deleted) as a potential fallback — specifically,
this stack avoids WS_EX_LAYERED (using WM_NCHITTEST alone for click-through)
because of a previously measured 196->147 FPS regression attributed to that
flag. The shipped stack (dx11_overlay.py) later added WS_EX_LAYERED back to
fix a click-through bug, and as of this writing has NOT verified whether that
FPS regression actually reproduces on the shipped window architecture in a
GPU-bound game (see the "KNOWN TRADEOFF — NOT YET VERIFIED" comment block in
dx11_overlay.py's _create_window). If that tradeoff ever turns out to be a
real problem for the shipped stack, this WM_NCHITTEST-only approach is the
documented alternative to fall back to or investigate further — though note
dx11_overlay.py's own comments record that a live SendInput test found
WM_NCHITTEST-alone did NOT reliably achieve real click-through on that
window architecture, so this stack's click-through has not been independently
re-verified as actually working either.

Because this stack is frozen, it has been allowed to drift behind main.py and
does NOT have feature parity as of this point in the project. Known gaps
include (non-exhaustive): no FPS tracking, no running indicator, no
auto-update-check, and its recoil strength slider is still hardcoded to the
old 1-20 range instead of the current 1-99 range used by the shipped stack.
Do not "fix" these gaps to chase parity — this snapshot is deliberately not
actively maintained. Any future work reviving this stack should treat it as
a full re-sync against main.py, not an incremental patch.
"""
import subprocess
import sys

from crash_logging import setup_logging

_INTERCEPTION_SERVICES = ["keyboard_filter", "mouse_filter"]


def _interception_driver(start: bool) -> None:
    action = "start" if start else "stop"
    for svc in _INTERCEPTION_SERVICES:
        subprocess.run(["sc", action, svc], capture_output=True)


import profiles as prof
from recoil       import RecoilEngine
from macro_engine import MacroEngine
from stats_poller import StatsPoller
from imgui_overlay import OverlayApp


def main():
    setup_logging()
    _interception_driver(start=True)

    profile_data = prof.load()
    cfg          = prof.activeSettings(profile_data)

    # Always start with active modules disabled for safety
    cfg["recoil"]["enabled"]    = False
    cfg["crosshair"]["enabled"] = False
    cfg["remapper"]["enabled"]  = False
    cfg.setdefault("stats", {})["enabled"] = False

    engine       = RecoilEngine(cfg)
    macro_engine = MacroEngine(cfg)
    engine.setMacroEngine(macro_engine)
    stats_poller = StatsPoller(cfg)

    app = OverlayApp(cfg, profile_data, engine, macro_engine,
                     on_settings_changed=_make_on_changed(
                         engine, macro_engine, stats_poller))

    # Wire engine callbacks → app queue (thread-safe)
    engine.setOverlayCallback(app.queue.put_overlay_toggled)
    engine.setToggleCallback(app.queue.put_recoil_toggled)
    engine.setStrengthCallback(app.queue.put_strength_changed)
    engine.setQuitCallback(app.queue.put_quit)

    stats_poller.setCallback(app.queue.put_stats_updated)

    engine.start()
    macro_engine  # MacroEngine starts automatically in __init__ (same as before)
    stats_poller.start()

    # Blocking render loop — returns when user quits
    app.run()

    engine.stop()
    macro_engine.stop()
    stats_poller.stop()
    _interception_driver(start=False)


def _make_on_changed(engine, macro_engine, stats_poller):
    def on_settings_changed(updated: dict):
        engine.updateSettings(updated)
        macro_engine.updateSettings(updated)
        stats_poller.updateSettings(updated)
    return on_settings_changed


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close...")
