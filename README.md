# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver.

---

## Modules

### Recoil Compensation

Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil.

- Strength is adjustable from 1–99 via the overlay panel or in-game hotkeys
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

### Button Remapper

Remap any keyboard key or mouse button to any other keyboard key, mouse button, or scroll wheel action. Supports X1/X2 side buttons and scroll as both source and destination.

- Multiple mappings can be active simultaneously
- **Enabled checkbox** always defaults to off on startup and on profile load, regardless of saved state
- Protected hotkeys (Menu Toggle, Quit) cannot be used as remap sources
- **Conflict prevention** — you can't bind the same key or button to two different things at once. If you try to capture a key that's already used elsewhere (a hotkey, a Recoil/Rapid Fire trigger or slot key, another remap, or a macro trigger), the capture is rejected and you're told what it's already bound to. You can immediately try a different key — no need to restart the binding process.
- **Remapped input also triggers other modules** — if you remap a key (say `1`) to `mouse_left`, that remap not only makes the game see a left-click, it also counts as `mouse_left` for R9Tools's own purposes. Any Recoil, Rapid Fire, or Macro trigger configured to fire on `mouse_left` will now also respond when you press `1`. This is a real capability (chain a remap into other modules) but also worth knowing if you have overlapping bindings, since it means remapped keys are no longer "invisible" to the rest of R9Tools.

When a **Window Filter** is set in Settings, remapping only fires while that window has focus. If the filter is left blank, remapping is always active when enabled.

---

### Overlay

The **Overlay** tab is home to everything that gets drawn on top of your game: the Crosshair, the Module Indicators, and the Stats Overlay. They're independent features that just happen to share one panel tab now — enabling or configuring one has no effect on the others.

#### Crosshair

Draws a persistent crosshair at screen center, independent of the overlay menu. Style, color, size, thickness, gap, and outline are all configurable.

**Styles:** Cross, Dot, Dot + Cross, Circle, Circle + Dot

When a **Window Filter** is set in Settings, the crosshair will only appear while that window has focus. If the filter is left blank, the crosshair is always visible when enabled.

#### Module Indicators

Small on-screen text that shows which modules are currently active — `R` for Recoil Compensation, `RF` for Rapid Fire. Useful for confirming at a glance that a module is armed without opening the overlay menu.

- **Enable/disable toggle** — turn the indicators on or off independently of the crosshair
- **Position** — choose from 6 options: the four screen corners, or anchored just above or below the crosshair
- Works whether or not the crosshair itself is currently shown
- When a **Window Filter** is set in Settings, Module Indicators follow the same rule as the Crosshair and only appear while that window has focus. If the filter is left blank, they're always visible when enabled.

#### Stats Overlay

Displays a small always-on-top, click-through hardware stats overlay in any corner of the screen. Powered by [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor).

**Metrics (each individually toggleable):**

- CPU usage % and temperature °C (shown on one line when both enabled)
- GPU usage % and temperature °C (shown on one line when both enabled)
- GPU VRAM used / total (GB)
- RAM used / total (GB)

**Configuration:**

- **Position** — place the overlay in any of 6 corners: top, middle, or bottom on either side
- **Update Rate** — 1–5 Hz
- **Opacity** — background alpha from 0–100%; text remains fully opaque at all times
- **Text Color** — six presets: White, Yellow, Cyan, Green, Orange, Red
- The overlay renders with rounded corners and a drop shadow that scales with the background opacity

The overlay requires `LibreHardwareMonitorLib.dll` in the `lib/` folder next to the executable. If it is not present, the Stats panel will show a notice and no data will be displayed. Use the **Check for Updates** button in Settings to download the DLL automatically.

> Stats are collected on a background thread and do not affect overlay or input performance.
>
> If you enable both the Stats Overlay and the "R9 Running" indicator (see the Settings section below) at their default top-right positions, they can visually overlap — there's currently no automatic collision avoidance between the two, so you may want to move the Stats Overlay to a different corner if that happens.

---

### Profiles

Save, load, and delete named configurations. All module settings are stored per-profile, making it easy to maintain separate setups per game.

- Select a profile from the dropdown, then click **Apply** to load it
- Use **Save** next to the dropdown to overwrite the current profile, or enter a new name and click **Save** to create one
- A protected **Default** profile is always available and cannot be deleted or overwritten
- Loading a profile always starts with recoil, crosshair, remapper, rapid fire, stats, and all macros disabled
- The top bar flashes to confirm saves (green), loads (blue), and deletes (red)

---

### Settings

Program-wide configuration accessible from the right side of the top bar.

**Window Filter** — select a process name (e.g. `game.exe`) from the running process list. When set, Recoil Compensation, the Crosshair, the Module Indicators, and the Button Remapper will only be active while that window has focus. Refresh the list with the ↻ button. Leave blank to have all modules act globally.

**Color Theme** — switch between Dark and Light themes. The selection is saved per-profile.

**R9 Running Indicator** — a simple on/off toggle for a small badge (a power/standby icon with "R9" next to it) fixed to the top-right corner of the screen at all times. It's separate from the Overlay tab's Module Indicators — its only job is to confirm R9Tools is loaded and running without needing to open the overlay menu. Defaults to **on**. See the note in the Stats Overlay section above about a possible overlap if you also run the Stats Overlay at its default top-right position.

**Hotkeys** — rebind all global hotkeys from a single panel:

| Hotkey | Default |
| --- | --- |
| Menu Toggle | Insert |
| Quit | Delete |
| Recoil Toggle | F10 |
| Recoil Strength − | `[` |
| Recoil Strength + | `]` |

**Updates** — check for and apply updates from within the app:

- **R9Tools** — checks the GitHub releases page for a newer version of the executable. Downloads and replaces the running exe; restart to apply.
- **LibreHardwareMonitor** — checks for a newer compatible LHM release and stages the updated DLLs. Changes take effect on the next launch.

> Updates only work in the packaged `.exe` build. Running from source requires manual updates.

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
- **Module Indicators** — only renders while the filtered window has focus, just like the Crosshair
- **Button Remapper** — remap rules only fire while the filtered window has focus

Leave the Window Filter **blank** to have these modules operate globally with no window restriction. The filter is saved per-profile, so each game profile can point to its own process.

---

## Important: Game Display Mode

The overlay requires your game to run in **Borderless Windowed** mode. Exclusive Fullscreen will prevent the overlay from rendering on top of the game.

> If your display resolution changes while R9Tools is running — for example, a game switching to a different exclusive-fullscreen resolution — the overlay, crosshair, and top bar now reposition themselves live to match. You no longer need to restart R9Tools after a resolution change to get things lined up correctly.

---

## Installation

### Option A — Installer (recommended)

Download `R9Tools_vX.X.X.zip` from the [Releases](https://github.com/VenusFallen/R9Tools/releases) page, extract it, and run `R9Tools_Setup.exe`. The installer will:

- Install R9Tools to `Program Files\R9Tools`
- Install the Interception kernel driver automatically
- Create a Start Menu shortcut

Reboot after installation, then launch R9Tools as **Administrator**.

To enable the Stats Overlay, open the **Settings** tab and click **Check for Updates** under LibreHardwareMonitor. The DLL will be downloaded and applied automatically on next launch.

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

```bashab
pip install PySide6 interception-python psutil pywin32 pythonnet
```

4. Download the LHM zip (not the `.NET 10` build) from the [LibreHardwareMonitor releases](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases) page and extract **every** `.dll` from it into a `lib/` folder next to `main.py` — `LibreHardwareMonitorLib.dll` is the main assembly, but its dependency DLLs are required too (required for the Stats Overlay; see `lib/PLACE_DLL_HERE.txt`)

5. Run as **administrator**:

```bash
python main.py
```

> `profiles.json` is created automatically on first run and is gitignored.

---

## Notes

- **`psutil` and `pywin32`** are required for the Window Filter feature (used by both Crosshair and Button Remapper). Both modules will still function without them, but window-specific filtering will be unavailable.
- **`pythonnet`** is required for the Stats Overlay. If not installed, the Stats panel will show a notice and no data will be displayed. Not needed for any other feature.
- **Interception driver lifecycle** — R9Tools automatically starts the Interception kernel driver (`keyboard_filter`, `mouse_filter`) on launch and stops it on clean exit. This means the driver is only loaded while the program is running, reducing its footprint when R9Tools is not in use. If the process is force-killed (e.g. via Task Manager), the driver will remain loaded until the next clean launch.
- **Anti-cheat compatibility** — R9Tools is **not compatible** with games protected by kernel-level anti-cheat (Easy Anti-Cheat, BattlEye). The Interception driver is a kernel filter driver and will be visible to these systems. Do not run R9Tools alongside games that use kernel anti-cheat. VAC (Steam) is userspace-only and is generally unaffected.
- **Antivirus false positives** — Windows Defender and other AV tools may flag this application due to the Interception kernel driver. The driver operates at the kernel level for input filtering only; this is a known false positive for Interception-based tools. You can submit the driver for analysis at [Microsoft's security portal](https://www.microsoft.com/en-us/wdsi/filesubmission) if needed.
- **Antivirus false positives** may also flag the pre-built EXE for the same reason as the driver.

---

## Logging (for when something goes wrong)

R9Tools keeps a log file that records what the app was doing, which is very helpful if you ever need to report a bug. You don't need to do anything to turn this on — it's always running in the background.

The log file lives at:

```text
%LOCALAPPDATA%\R9Tools\logs\r9tools.log
```

**If you're not sure what that means, here's how to get there:**

1. Open **File Explorer** (the folder icon in your taskbar).
2. Click once in the address bar at the top (where it shows a path like "This PC > ..."), so the text there gets selected.
3. Type or paste in `%LOCALAPPDATA%\R9Tools\logs` and press **Enter**.
4. This will open the folder containing the log file. On most PCs this is the same as `C:\Users\<your username>\AppData\Local\R9Tools\logs\r9tools.log`.

The log records things like: when R9Tools started and which version you're running, any unexpected error that happens on the main app, any unexpected error happening in the background (this used to be invisible — now it gets written down), and details about what was happening if the app crashes unexpectedly.

To avoid the log file growing forever, R9Tools automatically keeps up to 3 older backups alongside the current one (`r9tools.log.1`, `r9tools.log.2`, `r9tools.log.3`), rotating out the oldest as new ones are created.

**If R9Tools crashes, freezes, or does something unexpected:**

1. Go to the `%LOCALAPPDATA%\R9Tools\logs` folder using the steps above.
2. Report the issue on the [GitHub Issues page](https://github.com/VenusFallen/R9Tools/issues).
3. Attach `r9tools.log` to your report — and if there are `r9tools.log.1`, `.2`, or `.3` files present, attach those too, since they may contain the moment the problem actually happened.

Including this file makes it dramatically easier to figure out what went wrong, especially for problems that are hard to reproduce.
