# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver.

---

## Modules

### Recoil Compensation

Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil.

- Strength is adjustable from 1–30 via the overlay panel or in-game hotkeys
- Toggle on/off at any time without opening the overlay
- Trigger key and all hotkeys are rebindable from the Settings tab
- When a **Window Filter** is set in Settings, recoil compensation only fires while that window has focus

---

### Crosshair

Draws a persistent crosshair at screen center, independent of the overlay menu. Style, color, size, thickness, gap, and outline are all configurable.

**Styles:** Cross, Dot, Dot + Cross, Circle, Circle + Dot

When a **Window Filter** is set in Settings, the crosshair will only appear while that window has focus. If the filter is left blank, the crosshair is always visible when enabled.

---

### Button Remapper

Remap any keyboard key or mouse button to any other keyboard key, mouse button, or scroll wheel action. Supports X1/X2 side buttons and scroll as both source and destination.

- Multiple mappings can be active simultaneously
- **Enabled checkbox** always defaults to off on startup and on profile load, regardless of saved state
- Protected hotkeys (Menu Toggle, Quit) cannot be used as remap sources

When a **Window Filter** is set in Settings, remapping only fires while that window has focus. If the filter is left blank, remapping is always active when enabled.

---

### Profiles

Save, load, and delete named configurations. Settings and mappings are stored per-profile, making it easy to maintain separate setups per game.

- A protected **Default** profile is always available and cannot be deleted
- Loading a profile always starts with recoil, crosshair, and remapper disabled
- The top bar flashes to confirm saves (green), loads (blue), and deletes (red)

---

### Settings

Program-wide configuration accessible from the right side of the top bar.

**Window Filter** — select a process name (e.g. `game.exe`) from the running process list. When set, Recoil Compensation, the Crosshair, and the Button Remapper will only be active while that window has focus. Refresh the list with the ↻ button. Leave blank to have all modules act globally.

**Color Theme** — switch between Dark and Light themes. The selection is saved per-profile.

**Hotkeys** — rebind all global hotkeys from a single panel:

| Hotkey | Default |
| --- | --- |
| Menu Toggle | Insert |
| Quit | Delete |
| Recoil Toggle | F10 |
| Recoil Strength − | `[` |
| Recoil Strength + | `]` |

---

## Usage

1. Press **Insert** to show or hide the overlay (default)
2. Use **Left / Right arrow keys** to navigate tabs while the overlay is focused
3. All modules continue running in the background while the overlay is hidden
4. Configure hotkeys from the **Settings** tab; save your setup using the **Profiles** tab

> The overlay must be dismissed (hidden) before in-game input is fully restored — the overlay captures keyboard focus while visible.

---

## Window Filter

The Window Filter in the Settings tab restricts active modules to a specific game process:

- **Recoil Compensation** — pull only fires while the filtered window has focus
- **Crosshair** — only renders while the filtered window has focus; hides automatically when you alt-tab
- **Button Remapper** — remap rules only fire while the filtered window has focus

Leave the Window Filter **blank** to have all three modules operate globally with no window restriction. The filter is saved per-profile, so each game profile can point to its own process.

---

## Important: Game Display Mode

The overlay requires your game to run in **Borderless Windowed** mode. Exclusive Fullscreen will prevent the overlay from rendering on top of the game.

---

## Installation

### Option A — Installer (recommended)

Download `R9Tools_vX.X.X.zip` from the [Releases](https://github.com/VenusFallen/R9Tools/releases) page, extract it, and run `R9Tools_Setup.exe`. The installer will:

- Install R9Tools to `Program Files\R9Tools`
- Install the Interception kernel driver automatically
- Create a Start Menu shortcut

Reboot after installation, then launch R9Tools as **Administrator**.

### Option B — Run from source

**Requirements:**

- Windows 10 or 11
- Python 3.10+
- [Interception driver](https://github.com/oblitum/Interception) installed (run installer once, then reboot)
- Run as **Administrator** (required by the Interception driver)

**Steps:**

1. Install the Interception driver and reboot
2. Clone or download the repository
3. Install Python dependencies:

```bash
pip install interception-python psutil pywin32
```

1. Run as **administrator**:

```bash
python main.py
```

> `profiles.json` is created automatically on first run and is gitignored.

---

## Notes

- **`psutil` and `pywin32`** are required for the Window Filter feature (used by both Crosshair and Button Remapper). Both modules will still function without them, but window-specific filtering will be unavailable.
- **Antivirus false positives** — Windows Defender and other AV tools may flag this application due to the Interception kernel driver. The driver operates at the kernel level for input filtering only; this is a known false positive for Interception-based tools. You can submit the driver for analysis at [Microsoft's security portal](https://www.microsoft.com/en-us/wdsi/filesubmission) if needed.
- **Antivirus false positives** may also flag the pre-built EXE for the same reason as the driver.
