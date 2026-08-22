import json
import os
import copy
import shutil

# ---------------------------------------------------------------------------
# Persistent profiles.json location
# ---------------------------------------------------------------------------
# NOTE: previously this was `os.path.join(os.path.dirname(__file__), "profiles.json")`.
# That is NOT stable in a PyInstaller onefile build: `__file__` for a frozen
# module resolves inside the onefile extraction temp folder
# (`%TEMP%\_MEIxxxxxx\`), which gets a brand-new random name every single
# launch — so every real user's saved profile was silently discarded on
# their very next app launch. Use a stable, per-user, always-writable
# location instead, following the same directory-resolution pattern as
# crash_logging.py's `_default_log_dir()` (LOCALAPPDATA, falling back to
# TEMP/cwd, never raising). Use the same stable path whether running from
# source or frozen, for consistency.
_PROFILES_DIR_NAME = "R9Tools"
_PROFILES_FILE_NAME = "profiles.json"

# The OLD (pre-fix) location: next to wherever this module lives. For a
# source run this is the real repo directory (where a user's actual
# profiles.json may already exist); for a frozen run this was always an
# ephemeral onefile extraction dir and thus never had anything meaningful
# to migrate. Kept only so `load()` can migrate a pre-existing file forward
# one time — never written to going forward.
_OLD_PROFILES_FILE = os.path.join(os.path.dirname(__file__), _PROFILES_FILE_NAME)


def _default_profiles_dir() -> str:
    """Pick a per-user, reliably-writable directory for profiles.json.

    Mirrors crash_logging.py's `_default_log_dir()`: prefer
    %LOCALAPPDATA%\\R9Tools, falling back to a temp directory, then finally
    to the current working directory, if LOCALAPPDATA isn't set for some
    reason. Never raises.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
    return os.path.join(base, _PROFILES_DIR_NAME)


PROFILES_FILE = os.path.join(_default_profiles_dir(), _PROFILES_FILE_NAME)
DEFAULT_NAME = "Default"


def _migrate_old_profiles_file_if_needed() -> None:
    """One-time migration: if the new stable location doesn't have a
    profiles.json yet, but an old (pre-fix) one exists next to this module,
    copy it forward instead of silently creating fresh defaults.

    Copies (never moves/deletes) the old file — safer, and it's gitignored
    regardless so leaving it in place doesn't risk an accidental commit.
    Safe to call unconditionally; never raises.
    """
    try:
        if os.path.exists(PROFILES_FILE):
            return
        if not os.path.exists(_OLD_PROFILES_FILE):
            return
        if os.path.abspath(_OLD_PROFILES_FILE) == os.path.abspath(PROFILES_FILE):
            return
        os.makedirs(os.path.dirname(PROFILES_FILE), exist_ok=True)
        shutil.copy2(_OLD_PROFILES_FILE, PROFILES_FILE)
    except Exception:
        # Never let migration failure take down the app — worst case it
        # falls through to load()'s normal "create fresh defaults" path.
        pass

# ---------------------------------------------------------------------------
# Sanitization constants
# ---------------------------------------------------------------------------

_VALID_CROSSHAIR_STYLES = {"dot", "cross", "dot_cross", "circle", "circle_dot"}
_VALID_INDICATOR_POSITIONS = {
    "top_left", "top_right", "bottom_left", "bottom_right",
    "above_crosshair", "below_crosshair",
}
_VALID_MACRO_MODES      = {"once", "hold", "toggle"}
_VALID_MACRO_ACTIONS    = {"key_tap", "key_down", "key_up",
                           "mouse_click", "mouse_down", "mouse_up", "delay"}
_VALID_MOUSE_BUTTONS    = {"mouse_left", "mouse_right", "mouse_middle",
                           "mouse_x1", "mouse_x2"}

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
    "indicator": {
        "enabled": True,
        "position": "below_crosshair",
    },
    # Simple "app is alive" badge — deliberately separate from the module
    # indicator (R / RF) block above. No position option (always top-right).
    # Defaults on (unlike recoil/crosshair/remapper/RF) since it's a low-risk
    # informational badge meant to help exactly the non-technical users who'd
    # otherwise not know whether R9Tools loaded.
    "running_indicator": {
        "enabled": True,
    },
    # Whether to check GitHub for a newer R9Tools release a few seconds
    # after launch and prompt to update. A persistent app-behavior
    # preference (like running_indicator above), not a per-session overlay
    # feature — defaults on and is NOT forced off on profile load/startup.
    "auto_update_check": {
        "enabled": True,
    },
    "macros": [],
    "stats": {
        "enabled":        False,
        "corner":         "top_right",
        "update_rate_hz": 1,
        "show_cpu_usage": True,
        "show_cpu_temp":  True,
        "show_gpu_usage": True,
        "show_gpu_temp":  True,
        "show_gpu_vram":  True,
        "show_ram":       True,
        "show_fps":       True,
        "bg_alpha":       70,
        "text_color":     "#ffffff",
    },
}

_EMPTY = {
    "active": DEFAULT_NAME,
    "profiles": {
        DEFAULT_NAME: copy.deepcopy(_DEFAULT_SETTINGS)
    }
}


def _clamp_int(val, lo, hi, default):
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return default


def _sanitize_profile(profile: dict) -> None:
    """Clamp and validate all profile values in place.
    Defends against corrupted or hand-crafted profiles.json entries."""

    # Recoil
    rc = profile.get("recoil", {})
    rc["enabled"]     = bool(rc.get("enabled", False))
    rc["humanize"]    = bool(rc.get("humanize", False))
    rc["interval_ms"] = _clamp_int(rc.get("interval_ms", 10), 1, 1000, 10)
    if not isinstance(rc.get("weapons"), list):
        rc["weapons"] = [{"strength_y": 5}]
    clean_weapons = []
    for w in rc["weapons"]:
        if not isinstance(w, dict):
            continue
        w["strength_y"] = _clamp_int(w.get("strength_y", 5), 1, 99, 5)
        clean_weapons.append(w)
    if not clean_weapons:
        clean_weapons = [{"strength_y": 5}]
    rc["weapons"] = clean_weapons

    # Crosshair
    ch = profile.get("crosshair", {})
    ch["enabled"] = bool(ch.get("enabled", False))
    if ch.get("style") not in _VALID_CROSSHAIR_STYLES:
        ch["style"] = "cross"
    ch["size"]         = _clamp_int(ch.get("size", 10),        1,  100, 10)
    ch["thickness"]    = _clamp_int(ch.get("thickness", 2),    1,   20,  2)
    ch["gap"]          = _clamp_int(ch.get("gap", 3),          0,   50,  3)
    ch["outline_size"] = _clamp_int(ch.get("outline_size", 1), 0,   10,  1)

    # Module indicators (R / RF labels)
    ind = profile.get("indicator", {})
    ind["enabled"] = bool(ind.get("enabled", True))
    if ind.get("position") not in _VALID_INDICATOR_POSITIONS:
        ind["position"] = "below_crosshair"

    # Running indicator ("R9Tools is loaded" badge) — separate from the
    # module indicator above. No other fields to sanitize.
    ri = profile.get("running_indicator", {})
    ri["enabled"] = bool(ri.get("enabled", True))

    # Auto-update check on launch — persistent app-behavior preference, not
    # an overlay/session feature. No other fields to sanitize.
    auc = profile.get("auto_update_check", {})
    auc["enabled"] = bool(auc.get("enabled", True))

    # Hotkeys — each value must be {"code": 0-255, "e0": bool}
    hk = profile.get("hotkeys", {})
    for key in list(hk):
        bind = hk[key]
        if not isinstance(bind, dict):
            hk[key] = {"code": 0, "e0": False}
            continue
        code = bind.get("code", 0)
        if not isinstance(code, int) or not (0 <= code <= 255):
            bind["code"] = 0
        bind["e0"] = bool(bind.get("e0", False))

    # Remapper
    rm = profile.get("remapper", {})
    rm["enabled"] = bool(rm.get("enabled", False))
    if not isinstance(rm.get("mappings"), list):
        rm["mappings"] = []

    # Rapid fire
    rf = profile.get("rapidfire", {})
    rf["enabled"]     = bool(rf.get("enabled", False))
    rf["humanize"]    = bool(rf.get("humanize", False))
    rf["interval_ms"] = _clamp_int(rf.get("interval_ms", 100), 1, 10000, 100)

    # Stats
    st = profile.get("stats", {})
    st["enabled"]        = bool(st.get("enabled", False))
    st["update_rate_hz"] = _clamp_int(st.get("update_rate_hz", 1), 1, 5, 1)
    _VALID_CORNERS = {"top_left", "top_right", "middle_left", "middle_right",
                      "bottom_left", "bottom_right"}
    if st.get("corner") not in _VALID_CORNERS:
        st["corner"] = "top_right"
    for metric in ("show_cpu_usage", "show_cpu_temp", "show_gpu_usage",
                   "show_gpu_temp", "show_gpu_vram", "show_ram", "show_fps"):
        st[metric] = bool(st.get(metric, True))
    st["bg_alpha"] = _clamp_int(st.get("bg_alpha", 70), 0, 100, 70)
    _VALID_TEXT_COLORS = {"#ffffff", "#ffff00", "#00ffff", "#00ff00", "#ff8c00", "#ff4444"}
    if st.get("text_color") not in _VALID_TEXT_COLORS:
        st["text_color"] = "#ffffff"

    # Macros
    if not isinstance(profile.get("macros"), list):
        profile["macros"] = []
    clean_macros = []
    for macro in profile["macros"]:
        if not isinstance(macro, dict):
            continue
        macro["name"]     = str(macro.get("name", "Macro"))[:64]
        macro["enabled"]  = bool(macro.get("enabled", True))
        macro["humanize"] = bool(macro.get("humanize", False))
        if macro.get("mode") not in _VALID_MACRO_MODES:
            macro["mode"] = "once"
        if not isinstance(macro.get("actions"), list):
            macro["actions"] = []
        clean_actions = []
        for a in macro["actions"][:500]:  # hard cap — prevents runaway execution
            if not isinstance(a, dict):
                continue
            if a.get("type") not in _VALID_MACRO_ACTIONS:
                continue
            if a["type"] == "delay":
                a["ms"] = _clamp_int(a.get("ms", 50), 1, 60000, 50)
            elif a["type"] in ("key_tap", "key_down", "key_up"):
                code = a.get("code", 0)
                if not isinstance(code, int) or not (0 <= code <= 255):
                    continue
                a["e0"] = bool(a.get("e0", False))
            elif a["type"] in ("mouse_click", "mouse_down", "mouse_up"):
                if a.get("button") not in _VALID_MOUSE_BUTTONS:
                    continue
            clean_actions.append(a)
        macro["actions"] = clean_actions
        clean_macros.append(macro)
    profile["macros"] = clean_macros


def load() -> dict:
    _migrate_old_profiles_file_if_needed()

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

        # Migrate module indicator settings (new — backfill defaults for older
        # profiles.json entries that predate the indicator settings block)
        profile.setdefault("indicator", copy.deepcopy(_DEFAULT_SETTINGS["indicator"]))
        for key, val in _DEFAULT_SETTINGS["indicator"].items():
            profile["indicator"].setdefault(key, val)

        # Migrate running indicator settings (new — "R9Tools is loaded" badge,
        # separate from the module indicator block above; backfill defaults
        # for profiles.json entries that predate this feature).
        profile.setdefault("running_indicator", copy.deepcopy(_DEFAULT_SETTINGS["running_indicator"]))
        for key, val in _DEFAULT_SETTINGS["running_indicator"].items():
            profile["running_indicator"].setdefault(key, val)

        # Migrate auto-update-check settings (new — backfill defaults for
        # older profiles.json entries that predate this feature).
        profile.setdefault("auto_update_check", copy.deepcopy(_DEFAULT_SETTINGS["auto_update_check"]))
        for key, val in _DEFAULT_SETTINGS["auto_update_check"].items():
            profile["auto_update_check"].setdefault(key, val)

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

        # Migrate macros (new in V0.4.0 — backfill empty list)
        profile.setdefault("macros", [])

        # Migrate stats (new in V0.5.0 — backfill defaults)
        profile.setdefault("stats", copy.deepcopy(_DEFAULT_SETTINGS["stats"]))
        st = profile["stats"]
        for key, val in _DEFAULT_SETTINGS["stats"].items():
            st.setdefault(key, val)

        # Sanitize all values — clamp ranges, validate enums, strip bad action types
        _sanitize_profile(profile)

    return data


def save(data: dict) -> None:
    os.makedirs(os.path.dirname(PROFILES_FILE), exist_ok=True)
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
    settings.setdefault("stats", copy.deepcopy(_DEFAULT_SETTINGS["stats"]))
    settings["stats"]["enabled"]     = False
    settings.setdefault("indicator", copy.deepcopy(_DEFAULT_SETTINGS["indicator"]))
    settings["indicator"]["enabled"] = False
    # running_indicator is intentionally NOT forced off here — unlike the
    # other overlay toggles above, forcing this off on profile load would
    # defeat its purpose (confirming R9Tools loaded). It just keeps
    # whatever the profile's saved setting was.
    settings.setdefault("running_indicator", copy.deepcopy(_DEFAULT_SETTINGS["running_indicator"]))
    # auto_update_check is intentionally NOT forced off here either — same
    # reasoning as running_indicator above.
    settings.setdefault("auto_update_check", copy.deepcopy(_DEFAULT_SETTINGS["auto_update_check"]))
    settings.setdefault("rapidfire", copy.deepcopy(_DEFAULT_SETTINGS["rapidfire"]))
    settings["rapidfire"]["enabled"] = False
    settings.setdefault("macros", [])
    for macro in settings["macros"]:
        macro["enabled"] = False
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
