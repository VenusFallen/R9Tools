"""
R9Tools — imgui + Direct3D 11 entry point.
Run as administrator (required for Interception driver).

Replaces the PySide6 frontend with a single full-screen DX11 overlay window
that is eligible for hardware Multi-Plane Overlay (MPO), eliminating the
196→147 FPS regression caused by WS_EX_LAYERED GDI redirection surfaces.
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
