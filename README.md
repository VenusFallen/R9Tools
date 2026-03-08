# R9Tools

A personal gaming accessibility toolkit for Windows. Built with hardware-level input via the [Interception](https://github.com/oblitum/Interception) driver.

## Current Modules

### Recoil Compensation
Applies a configurable downward mouse pull while a trigger key combo is held, compensating for weapon recoil. Adjustable strength and interval, with rebindable trigger and toggle keys.

### Profiles
Save, load, and delete named configurations. A protected **Default** profile is always available. Loading a profile always starts with recoil disabled for safety. The top bar flashes to confirm saves (green), loads (blue), and deletes (red).

## Usage

Press **Insert** to show or hide the overlay. All modules continue running in the background while the overlay is hidden.

## Important: Game Display Mode

The overlay requires your game to run in **Borderless Windowed** mode. Exclusive Fullscreen will prevent the overlay from appearing on top of the game.

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
