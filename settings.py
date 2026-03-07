import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

DEFAULTS = {
    "recoil": {
        "enabled": False,
        "trigger_keys": ["mouse_left"],
        "toggle_key": 68,
        "strength_y": 5,
        "interval_ms": 10
    }
}


def load() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        save(DEFAULTS)
        return DEFAULTS

    with open(SETTINGS_FILE, "r") as f:
        data = json.load(f)

    for section, values in DEFAULTS.items():
        if section not in data:
            data[section] = values
        else:
            for key, default_val in values.items():
                if key not in data[section]:
                    data[section][key] = default_val

    return data


def save(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)
