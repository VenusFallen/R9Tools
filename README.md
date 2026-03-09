# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver.

## Modules

### Recoil Compensation

Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil. Strength is adjustable from the overlay or via hotkeys. Toggle key, trigger key, and strength hotkeys are all rebindable.

### Crosshair

Draws a persistent crosshair at screen center, independent of the overlay menu. Style, color, size, thickness, and gap are all configurable. Supports cross, dot, and circle styles with optional outline.

### Hotkeys

Rebind all global hotkeys from a single panel:

- **Menu Toggle** — show/hide the overlay (default: Insert)
- **Quit** — exit the program (default: Delete)
- **Recoil Toggle** — enable/disable recoil compensation (default: F10)
- **Recoil Strength −/+** — adjust strength while in-game (default: `[` / `]`)

### Button Remapper

Remap any keyboard key or mouse button to any other keyboard key, mouse button, or scroll wheel action. Supports X1/X2 side buttons and scroll as both source and destination.

- **Window filter** — optionally restrict remapping to a specific process (e.g. `game.exe`). When a process is selected, remapping only fires while that window has focus.
- **Enabled checkbox** — always defaults to off on startup and on profile load, regardless of saved state.
- Protected hotkeys (Menu Toggle, Quit) cannot be used as remap sources.

### Profiles

Save, load, and delete named configurations. A protected **Default** profile is always available. Loading a profile starts with recoil, crosshair, and remapper all disabled. The top bar flashes to confirm saves (green), loads (blue), and deletes (red).

## Usage

Press **Insert** to show or hide the overlay by default. All modules continue running in the background while the overlay is hidden. Use **Left/Right arrow keys** to navigate tabs while the overlay is focused.

## Important: Game Display Mode

The overlay requires your game to run in **Borderless Windowed** mode. Exclusive Fullscreen will prevent the overlay from appearing on top of the game.

## Requirements

- Windows 10/11
- Python 3.10+
- [Interception driver](https://github.com/oblitum/Interception) installed (run once, requires reboot)
- Run as **Administrator** (required by the Interception driver)

## Setup

1. Install the Interception driver and reboot
1. Clone the repo
1. Install Python dependencies:

```bash
pip install interception-python psutil pywin32
```

1. Run as **administrator**:

```bash
python main.py
```

> `profiles.json` is created automatically on first run and is gitignored.

## Notes

- `psutil` and `pywin32` are required for the Button Remapper's window filter feature. The remapper will still function without them, but window-specific filtering will be unavailable.
- Windows Defender or other AV software may flag this tool due to the Interception kernel driver. This is a false positive — the driver operates at the kernel level for input filtering only. You can submit the driver for analysis at [Microsoft's security portal](https://www.microsoft.com/en-us/wdsi/filesubmission) if needed.
- Source-only distribution is intentional. No pre-built executable is provided at this time.
