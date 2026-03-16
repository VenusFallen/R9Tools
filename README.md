# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver.

---

## Modules

### Recoil Compensation

Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil.

- Strength is adjustable from 1–30 via the overlay panel or in-game hotkeys
- **Humanize** adds subtle randomized variation to each pull to reduce pattern detection
- **Multi-weapon slots** — configure separate strength values per weapon, each bindable to a key, mouse button, or scroll wheel direction
- Toggle on/off at any time without opening the overlay
- Trigger key and all hotkeys are rebindable from the Settings tab
- When a **Window Filter** is set in Settings, recoil compensation only fires while that window has focus

---

### Rapid Fire

Automatically re-fires a held trigger button at a configurable interval, simulating rapid repeated clicks.

- Set any mouse button or key combo as the fire trigger
- **Slot keys** — bind keys, mouse buttons, or scroll directions to arm/disarm rapid fire independently
- Adjustable interval in milliseconds
- **Humanize** adds random timing jitter to each click
- Enabled checkbox always defaults to off on startup and profile load

---

### Macros

Record and play back sequences of keyboard and mouse actions, triggered by any key or mouse button.

- **Three execution modes:**
  - **Once** — fires once on trigger release
  - **Hold** — runs continuously while the trigger is held
  - **Toggle** — starts on first press, stops on second press (loops)
- **Recording** — capture real input with accurate timing; press Stop to convert to an editable action list
- **Manual action builder** — add Key Tap, Key Down, Key Up, Mouse Click, Mouse Down, Mouse Up, and Delay steps individually via menu
- **Delay steps** are editable inline after recording
- **Humanize** adds random jitter to all delay timings during playback
- Per-macro enable toggle — disable individual macros without deleting them
- **Test** button fires the macro immediately from the editor
- Macros are saved per-profile

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

Save, load, and delete named configurations. All module settings are stored per-profile, making it easy to maintain separate setups per game.

- Select a profile from the dropdown, then click **Apply** to load it
- Use **Save** next to the dropdown to overwrite the current profile, or enter a new name and click **Save** to create one
- A protected **Default** profile is always available and cannot be deleted or overwritten
- Loading a profile always starts with recoil, crosshair, remapper, and rapid fire disabled
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
pip install PySide6 interception-python psutil pywin32
```

1. Run as **administrator**:

```bash
python main.py
```

> `profiles.json` is created automatically on first run and is gitignored.

---

## Notes

- **`psutil` and `pywin32`** are required for the Window Filter feature (used by both Crosshair and Button Remapper). Both modules will still function without them, but window-specific filtering will be unavailable.
- **Interception driver lifecycle** — R9Tools automatically starts the Interception kernel driver (`keyboard_filter`, `mouse_filter`) on launch and stops it on clean exit. This means the driver is only loaded while the program is running, reducing its footprint when R9Tools is not in use. If the process is force-killed (e.g. via Task Manager), the driver will remain loaded until the next clean launch.
- **Anti-cheat compatibility** — R9Tools is **not compatible** with games protected by kernel-level anti-cheat (Easy Anti-Cheat, BattlEye). The Interception driver is a kernel filter driver and will be visible to these systems. Do not run R9Tools alongside games that use kernel anti-cheat. VAC (Steam) is userspace-only and is generally unaffected.
- **Antivirus false positives** — Windows Defender and other AV tools may flag this application due to the Interception kernel driver. The driver operates at the kernel level for input filtering only; this is a known false positive for Interception-based tools. You can submit the driver for analysis at [Microsoft's security portal](https://www.microsoft.com/en-us/wdsi/filesubmission) if needed.
- **Antivirus false positives** may also flag the pre-built EXE for the same reason as the driver.
