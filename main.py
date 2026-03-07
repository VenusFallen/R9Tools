"""
R9Tools - Gaming Accessibility Toolkit
Run as administrator (required for Interception driver).
"""
import settings
from recoil import RecoilEngine
from overlay import Overlay


def main():
    cfg = settings.load()
    cfg["recoil"]["enabled"] = False  # always start disabled

    engine = RecoilEngine(cfg)
    engine.start()

    def onSettingsChanged(updated: dict):
        engine.updateSettings(updated)
        settings.save(updated)

    overlay = Overlay(cfg, engine, onSettingsChanged)

    engine.setToggleCallback(overlay.setEnabled)

    overlay.run()

    engine.stop()


if __name__ == "__main__":
    main()
