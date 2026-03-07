# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver to operate independently of anti-cheat software.

## Current Modules

### Recoil Compensation
Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil. Features an always-on-top overlay UI with live status, adjustable strength, and rebindable keys.

## Requirements

- Windows 10/11
- Python 3.10+
- [Interception driver](https://github.com/oblitum/Interception) installed (run once, requires reboot)
- `interception-python` library

```bash
pip install interception-python
```

## Setup

1. Install the Interception driver and reboot
2. Clone the repo
3. Install dependencies
4. Run as **administrator**:

```bash
python main.py
```

## Usage

| Control | Action |
|---|---|
| Checkbox | Enable / disable recoil compensation |
| F10 | Toggle on/off via hotkey (rebindable) |
| Trigger button | Set the key/button combo that activates recoil while held |
| Toggle button | Rebind the on/off hotkey |
| Pull Strength | Pixels of downward pull per interval tick |
| Interval (ms) | How frequently the pull is applied |
| ✕ | Close the overlay |

Settings are saved automatically to `settings.json`.

## Project Structure

```
R9Tools/
├── main.py          # Entry point
├── recoil.py        # Recoil engine + input listener
├── overlay.py       # Tkinter overlay UI
├── settings.py      # Config load/save
└── tests/           # API validation and driver tests
```
