import json
import os
import copy

PROFILES_FILE = os.path.join(os.path.dirname(__file__), "profiles.json")
DEFAULT_NAME = "Default"

_DEFAULT_SETTINGS = {
    "theme": "Dark",
    "window_filter": "",
    "recoil": {
        "enabled": False,
        "trigger_keys": ["mouse_left"],
        "strength_y": 5,
        "interval_ms": 10,
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
