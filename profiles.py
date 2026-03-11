import json
import os
import copy

PROFILES_FILE = os.path.join(os.path.dirname(__file__), "profiles.json")
DEFAULT_NAME = "Default"

_DEFAULT_SETTINGS = {
    "theme": "Dark",
    "window_filter": "",
    "recoil": {
        "enabled":      False,
        "trigger_keys": ["mouse_left"],
        "humanize":     False,
        "interval_ms":  10,
        "weapons":      [{"strength_y": 5}],
    },
    "crosshair": {
        "enabled": False,
        "style": "cross",
        "color": "green",
        "size": 10,
        "thickness": 2,
        "gap": 3,
        "outline_size": 1,
    },
    "hotkeys": {
        "overlay_toggle":       {"code": 82, "e0": True},
        "recoil_toggle":        {"code": 68, "e0": False},
        "recoil_strength_down": {"code": 26, "e0": False},
        "recoil_strength_up":   {"code": 27, "e0": False},
        "quit":                 {"code": 83, "e0": True},
    },
    "remapper": {
        "enabled":  False,
        "mappings": [],
    },
    "rapidfire": {
        "enabled":      False,
        "trigger_keys": ["mouse_left"],
        "slot_keys":    [],
        "interval_ms":  100,
        "humanize":     False,
    },
}

_EMPTY = {
    "active": DEFAULT_NAME,
    "profiles": {
        DEFAULT_NAME: copy.deepcopy(_DEFAULT_SETTINGS)
    }
}


def load() -> dict:
    if not os.path.exists(PROFILES_FILE):
        save(copy.deepcopy(_EMPTY))
        return copy.deepcopy(_EMPTY)

    with open(PROFILES_FILE, "r") as f:
        data = json.load(f)

    # Ensure Default always exists and is complete
    data.setdefault("profiles", {})
    data.setdefault("last_tab", 4)   # 4 = Settings tab; shown on first launch
    if DEFAULT_NAME not in data["profiles"]:
        data["profiles"][DEFAULT_NAME] = copy.deepcopy(_DEFAULT_SETTINGS)

    # Ensure active points to a real profile
    if data.get("active") not in data["profiles"]:
        data["active"] = DEFAULT_NAME

    for profile in data["profiles"].values():
        # Migrate crosshair defaults
        profile.setdefault("crosshair", copy.deepcopy(_DEFAULT_SETTINGS["crosshair"]))
        for key, val in _DEFAULT_SETTINGS["crosshair"].items():
            profile["crosshair"].setdefault(key, val)

        # Migrate hotkeys: promote old recoil.toggle_key int → hotkeys.recoil_toggle dict
        old_toggle = profile.get("recoil", {}).pop("toggle_key", None)
        profile.setdefault("hotkeys", copy.deepcopy(_DEFAULT_SETTINGS["hotkeys"]))
        if old_toggle is not None:
            profile["hotkeys"]["recoil_toggle"] = {"code": old_toggle, "e0": False}
        for key, val in _DEFAULT_SETTINGS["hotkeys"].items():
            profile["hotkeys"].setdefault(key, copy.deepcopy(val))

        # Migrate top-level window_filter (moved out of remapper)
        profile.setdefault("theme", "Dark")
        profile.setdefault("window_filter", "")
        profile.get("remapper", {}).pop("window_filter", None)  # remove old location

        # Migrate remapper defaults
        profile.setdefault("remapper", copy.deepcopy(_DEFAULT_SETTINGS["remapper"]))
        for key, val in _DEFAULT_SETTINGS["remapper"].items():
            profile["remapper"].setdefault(key, copy.deepcopy(val) if isinstance(val, (dict, list)) else val)

        # Migrate recoil defaults (backfills humanize, interval_ms, etc. into old profiles)
        profile.setdefault("recoil", copy.deepcopy(_DEFAULT_SETTINGS["recoil"]))
        rc = profile["recoil"]
        # Convert old flat strength_y → weapons list
        if "strength_y" in rc and "weapons" not in rc:
            rc["weapons"] = [{"strength_y": rc.pop("strength_y")}]
        elif "strength_y" in rc:
            rc.pop("strength_y")
        for key, val in _DEFAULT_SETTINGS["recoil"].items():
            rc.setdefault(key, copy.deepcopy(val) if isinstance(val, (dict, list)) else val)

        # Migrate rapidfire defaults
        profile.setdefault("rapidfire", copy.deepcopy(_DEFAULT_SETTINGS["rapidfire"]))
        rf = profile["rapidfire"]
        # Convert old single trigger_key dict → trigger_keys list
        if "trigger_key" in rf:
            old_trig = rf.pop("trigger_key")
            if "trigger_keys" not in rf:
                if old_trig.get("type") == "mouse":
                    rf["trigger_keys"] = [old_trig.get("button", "mouse_left")]
                else:
                    rf["trigger_keys"] = ["mouse_left"]
        # Convert old single slot_key → slot_keys list
        if "slot_key" in rf:
            old_sk = rf.pop("slot_key")
            if "slot_keys" not in rf:
                # Only migrate if it was actually bound
                if old_sk.get("code", 0) or old_sk.get("type") in ("mouse", "scroll"):
                    old_sk.setdefault("enabled", True)
                    old_sk.setdefault("type", "key")
                    rf["slot_keys"] = [old_sk]
                else:
                    rf["slot_keys"] = []
        # Backfill any missing keys from defaults
        for key, val in _DEFAULT_SETTINGS["rapidfire"].items():
            rf.setdefault(key, copy.deepcopy(val) if isinstance(val, (dict, list)) else val)

        # Remove old rf_engage hotkey (slot key now lives in rapidfire.slot_key)
        profile["hotkeys"].pop("rf_engage", None)

    return data


def save(data: dict) -> None:
    with open(PROFILES_FILE, "w") as f:
        json.dump(data, f, indent=4)


def activeSettings(data: dict) -> dict:
    """Returns a deep copy of the active profile's settings (safe to mutate as live config)."""
    return copy.deepcopy(data["profiles"][data["active"]])


def loadProfile(data: dict, name: str) -> dict | None:
    """Set active profile and return its settings with enabled forced False.
    Returns None if profile not found."""
    if name not in data["profiles"]:
        return None
    data["active"] = name
    settings = copy.deepcopy(data["profiles"][name])
    settings["recoil"]["enabled"]    = False
    settings["crosshair"]["enabled"] = False
    settings["remapper"]["enabled"]  = False
    settings.setdefault("rapidfire", copy.deepcopy(_DEFAULT_SETTINGS["rapidfire"]))
    settings["rapidfire"]["enabled"] = False
    save(data)
    return settings


def saveProfile(data: dict, name: str, settings: dict) -> bool:
    """Save current settings snapshot under name.
    Returns False if name is Default or empty."""
    name = name.strip()
    if not name or name == DEFAULT_NAME:
        return False
    data["profiles"][name] = copy.deepcopy(settings)
    data["active"] = name
    save(data)
    return True


def deleteProfile(data: dict, name: str) -> bool:
    """Delete a profile. Returns False if name is Default or doesn't exist."""
    if name == DEFAULT_NAME or name not in data["profiles"]:
        return False
    del data["profiles"][name]
    if data["active"] == name:
        data["active"] = DEFAULT_NAME
    save(data)
    return True


def profileNames(data: dict) -> list[str]:
    """Profile names with Default always first, rest alphabetical."""
    rest = sorted(n for n in data["profiles"] if n != DEFAULT_NAME)
    return [DEFAULT_NAME] + rest
