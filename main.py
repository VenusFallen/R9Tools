"""
R9Tools - Gaming Accessibility Toolkit
Run as administrator (required for Interception driver).
"""
import profiles as prof
from recoil import RecoilEngine
from overlay import Overlay


def main():
    profileData = prof.load()
    cfg = prof.activeSettings(profileData)
    cfg["recoil"]["enabled"] = False  # always start disabled

    engine = RecoilEngine(cfg)
    engine.start()

    def onSettingsChanged(updated: dict):
        engine.updateSettings(updated)
        # No auto-save — profiles require explicit save via the Profiles panel

    overlay = Overlay(cfg, profileData, engine, onSettingsChanged)

    engine.setToggleCallback(overlay.setEnabled)
    engine.setOverlayCallback(overlay.toggleOverlay)
    engine.setStrengthCallback(overlay.onStrengthChanged)

    overlay.run()

    engine.stop()


if __name__ == "__main__":
    main()
