"""
Shared keybind-conflict detection.

Every place in the app where the user captures a key / mouse-button /
scroll-direction binding (recoil trigger, recoil weapon slots, rapid-fire
trigger, rapid-fire slots, remapper FROM sources, macro triggers,
hold-to-toggle bindings, global hotkeys) should hard-block a capture that
collides with a binding already used somewhere else, rather than silently
allowing the same physical key to be double-booked. Double-booking used to be possible and is exactly what
produced undocumented "last branch wins" behaviour in recoil.py's stroke
handling (e.g. a key bound to both an RF slot and a remap source would never
reach the remapper, since the RF slot check runs first and returns early).

This module normalizes the several binding shapes used across the codebase
into a single canonical signature so they can be compared against each
other, and provides `findConflict()` for capture widgets to call after a key
is captured but before it is committed to settings.

Binding shapes normalized here:
  - keyboard: {"code": int, "e0": bool}                    (type omitted)
              {"type": "key", "code": int, "e0": bool}      (type explicit)
  - mouse:    {"type": "mouse", "button": "mouse_left"}
  - scroll:   {"type": "scroll", "direction": "up"/"down"}
  - combo:    ["W", "LMB", ...]   — recoil/RF trigger-key capture stores a
              *list* of tokens (each either a raw mouse key name like
              "mouse_left" or a human-readable scancode label produced by
              recoil.scancodeLabel()) rather than a single binding dict.
  - unset/empty (None, {}, [], or a dict with no code/button/direction)
              always normalizes to "no signature" and never conflicts with
              anything, including another unset binding.
"""

from recoil import MOUSE_BUTTON_FLAGS, SCANCODE_NAMES, SCANCODE_NAMES_E0

_MOUSE_DISPLAY = {
    "mouse_left":   "LMB",
    "mouse_right":  "RMB",
    "mouse_middle": "MMB",
    "mouse_x1":     "Mouse4",
    "mouse_x2":     "Mouse5",
}

_HOTKEY_DISPLAY = {
    "overlay_toggle":       "Menu Toggle",
    "quit":                 "Quit",
    "recoil_toggle":        "Recoil Toggle",
    "recoil_strength_up":   "Strength +",
    "recoil_strength_down": "Strength -",
}

# Reverse lookup used only for combo tokens (recoil/RF trigger capture
# stores each held key as a scancodeLabel() *string* instead of a
# {"code", "e0"} dict) — label -> (code, e0).
_LABEL_TO_CODE = {}
for _code, _name in SCANCODE_NAMES.items():
    _LABEL_TO_CODE[_name] = (_code, False)
for _code, _name in SCANCODE_NAMES_E0.items():
    _LABEL_TO_CODE.setdefault(_name, (_code, True))


def _labelToSig(token: str):
    """Reverse a single combo token back into a canonical signature tuple.
    Returns None if the token can't be resolved (shouldn't normally happen,
    but capture is best-effort so we degrade gracefully)."""
    if token in MOUSE_BUTTON_FLAGS:
        return ("mouse", token)
    if token in _LABEL_TO_CODE:
        code, e0 = _LABEL_TO_CODE[token]
        return ("key", code, e0)
    # Fallback: scancodeLabel()'s format for unmapped codes is "SC{n}" /
    # "SC{n}e0" — parse those directly rather than giving up.
    if isinstance(token, str) and token.startswith("SC"):
        body = token[2:]
        e0 = body.endswith("e0")
        if e0:
            body = body[:-2]
        if body.isdigit():
            return ("key", int(body), e0)
    return None


def bindingSignatures(binding) -> list:
    """Normalize any binding shape used across the app into a list of
    canonical (kind, ...) signature tuples.

    A combo (list of tokens, used only by the recoil/RF trigger-key
    capture) can yield multiple signatures — one per held key. Every other
    binding shape yields at most one. Unset/empty bindings yield an empty
    list so they never register as a conflict.
    """
    if not binding:
        return []
    if isinstance(binding, (list, tuple)):
        sigs = []
        for tok in binding:
            sig = _labelToSig(tok)
            if sig is not None:
                sigs.append(sig)
        return sigs
    if isinstance(binding, dict):
        t = binding.get("type")
        if t == "mouse":
            button = binding.get("button")
            return [("mouse", button)] if button else []
        if t == "scroll":
            direction = binding.get("direction")
            return [("scroll", direction)] if direction else []
        # type == "key", or type omitted entirely — keyboard bindings from
        # KeybindButton / recoil weapon+RF-slot capture don't set "type".
        code = binding.get("code")
        if code is None:
            return []
        return [("key", code, bool(binding.get("e0", False)))]
    return []


def _remapInputLabel(inp: dict) -> str:
    t = inp.get("type", "")
    if t == "key":
        from recoil import scancodeLabel
        return scancodeLabel(inp.get("code", 0), inp.get("e0", False))
    if t == "mouse":
        return _MOUSE_DISPLAY.get(inp.get("button", ""), inp.get("button", "?"))
    if t == "scroll":
        return "Scroll Up" if inp.get("direction") == "up" else "Scroll Down"
    return "?"


def iterBindingSources(settings: dict):
    """Yield (exclude_id, label, signatures) for every binding capture
    point in the app.

    `exclude_id` is a stable identity token — callers pass the matching
    token back into findConflict()'s `exclude_id` param when re-capturing
    that exact field, so a field doesn't conflict with its own current
    value (e.g. re-capturing the same trigger key for the same weapon
    slot). Ids are built from object identity for list-of-dict entries
    (stable across in-place edits — every editor in this codebase mutates
    the dict in place via .clear()/.update() rather than replacing it,
    except remapper mappings which replace "from" but not the mapping
    dict itself) and from the settings key name for singleton fields.
    """
    recoil = settings.get("recoil", {}) or {}
    yield ("recoil_trigger", "Recoil Trigger",
           bindingSignatures(recoil.get("trigger_keys", [])))

    for i, w in enumerate(recoil.get("weapons", []) or []):
        yield (f"weapon:{id(w)}", f"Weapon {i + 1} Slot Key", bindingSignatures(w))

    rf = settings.get("rapidfire", {}) or {}
    yield ("rf_trigger", "RF Fire Trigger",
           bindingSignatures(rf.get("trigger_keys", [])))

    for i, sk in enumerate(rf.get("slot_keys", []) or []):
        yield (f"rf_slot:{id(sk)}", f"RF Slot Key {i + 1}", bindingSignatures(sk))

    remapper = settings.get("remapper", {}) or {}
    for mapping in remapper.get("mappings", []) or []:
        frm = mapping.get("from", {}) or {}
        to  = mapping.get("to", {}) or {}
        label = f"Remap: {_remapInputLabel(frm)} → {_remapInputLabel(to)}"
        yield (f"remap_from:{id(mapping)}", label, bindingSignatures(frm))

    for macro in settings.get("macros", []) or []:
        name = macro.get("name", "Macro")
        yield (f"macro_trigger:{id(macro)}", f"Macro: {name}",
               bindingSignatures(macro.get("trigger", {})))

    for t in settings.get("toggles", []) or []:
        label = t.get("name") or _remapInputLabel(t)
        yield (f"toggle:{id(t)}", f"Toggle: {label}", bindingSignatures(t))

    hotkeys = settings.get("hotkeys", {}) or {}
    for name, bind in hotkeys.items():
        display = _HOTKEY_DISPLAY.get(name, name)
        yield (f"hotkey:{name}", f"Hotkey: {display}", bindingSignatures(bind))


def hotkeyLabel(name: str) -> str:
    """Build the exact label iterBindingSources() yields for a hotkey
    settings key (e.g. "overlay_toggle" -> "Hotkey: Menu Toggle")."""
    return f"Hotkey: {_HOTKEY_DISPLAY.get(name, name)}"


# Hotkeys that must stay in a hard, non-overridable mutual exclusion with
# the remapper's FROM sources and toggle bindings, in both directions:
# they're the user's "escape hatch" out of a bad remap/recoil/toggle state,
# so unlike every other conflict type (which is warn-and-confirm), colliding
# with either of these can never be confirmed through — it's always
# reverted. Every other hotkey (recoil_toggle, recoil_strength_up/down)
# uses the normal warn-and-confirm flow like any other binding.
PROTECTED_REMAP_HOTKEYS = ("overlay_toggle", "quit")

# The exact conflict labels findConflict() returns when a candidate
# binding collides with one of the protected hotkeys above. Computed from
# _HOTKEY_DISPLAY rather than hardcoded so it always matches whatever
# iterBindingSources() actually yields.
PROTECTED_REMAP_LABELS = {hotkeyLabel(name) for name in PROTECTED_REMAP_HOTKEYS}


def isProtectedSourceConflictLabel(label) -> bool:
    """True if `label` (as returned by findConflict()) identifies a
    collision with an existing remapper mapping's FROM source or an
    existing toggle binding, i.e. it was built from either the
    "Remap: {fromLabel} -> {toLabel}" or "Toggle: {name}" template in
    iterBindingSources(). Menu Toggle / Quit hard-block on both of these
    source types (see PROTECTED_REMAP_HOTKEYS above)."""
    return isinstance(label, str) and (
        label.startswith("Remap:") or label.startswith("Toggle:")
    )


def findConflict(settings: dict, binding, exclude_id=None):
    """Check `binding` (any shape accepted by bindingSignatures — a single
    binding dict or a combo list) against every registered binding source
    in `settings`.

    Returns the human-readable label of the first conflicting source (e.g.
    "RF Slot Key 2", "Hotkey: Quit", "Remap: A -> B"), or None if the
    binding is free to use. Pass `exclude_id` (the same token
    iterBindingSources() yields for the field being edited) so re-capturing
    a field with its own current value isn't reported as a conflict with
    itself. An empty/unset candidate binding never conflicts with anything.

    Note: global hotkeys (Menu Toggle, Quit, Recoil Toggle, Strength +/-)
    always have a concrete binding, so a remap-FROM or toggle capture
    landing on any of them is naturally reported as e.g. "Hotkey: Menu
    Toggle" / "Hotkey: Quit" / "Hotkey: Recoil Toggle" without any
    special-casing here. Callers decide what to do with that label: most
    conflicts (including Recoil Toggle / Strength +/-) are warn-and-confirm,
    but Menu Toggle and Quit specifically are a hard, non-overridable mutual
    exclusion with the remapper's FROM sources and toggle bindings, in both
    directions — see PROTECTED_REMAP_LABELS / isProtectedSourceConflictLabel()
    above, used by panels/remapper.py, panels/recoil.py, and theme.py's
    KeybindButton respectively.
    """
    candidate = set(bindingSignatures(binding))
    if not candidate:
        return None
    for src_id, label, sigs in iterBindingSources(settings):
        if exclude_id is not None and src_id == exclude_id:
            continue
        if candidate & set(sigs):
            return label
    return None
