"""
StatsPoller — polls hardware stats via LibreHardwareMonitor on a background thread.

Requirements:
  pip install pythonnet
  lib/LibreHardwareMonitorLib.dll  (from github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)

Falls back gracefully if either is missing — stats simply won't appear.

Also tracks FPS of whichever window currently has OS focus via Intel's
PresentMon (presentmon/PresentMon.exe, bundled standalone console exe).
FPS tracking is intentionally NOT scoped by settings["window_filter"] — like
the rest of the stats overlay, it always reflects the real foreground window
regardless of which process Recoil/Remapper/Macros are filtered to.
"""
import logging
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

try:
    import win32gui
    import win32process
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False

_logger = logging.getLogger("r9tools.stats")

_lhm_available = False
_Computer      = None

# ---------------------------------------------------------------------------
# PresentMon / FPS tracking
# ---------------------------------------------------------------------------
_FPS_ROLLING_WINDOW = 30     # frames averaged for the smoothed FPS value
_FPS_STALE_SEC       = 2.0   # no fresh sample in this long -> report no FPS
_PID_DEBOUNCE_SEC    = 0.75  # foreground pid must be stable this long before retargeting

_pm_missing_warned      = False   # log "binary not found" only once per session
_pm_launch_failed_warned = False  # log "failed to launch" only once per session


def _presentmon_path() -> Path:
    """Resolve presentmon/PresentMon.exe using the exact same dev-mode vs.
    frozen (sys._MEIPASS) resolution pattern _bootstrap() uses for lib/."""
    try:
        if getattr(sys, "frozen", False):
            _persistent = Path(sys.executable).parent / "presentmon"
            _bundled    = Path(sys._MEIPASS) / "presentmon"
            pm_dir = (_persistent
                      if (_persistent / "PresentMon.exe").exists()
                      else _bundled)
        else:
            pm_dir = Path(__file__).parent / "presentmon"
    except Exception:
        pm_dir = Path(__file__).parent / "presentmon"
    return pm_dir / "PresentMon.exe"


def _foreground_pid():
    """Raw PID of whatever window currently has OS focus, or None.

    Mirrors recoil.py's windowMatchesFilter() (same win32gui/win32process
    calls) but returns the PID itself rather than a filter-match bool —
    FPS tracking always follows the real foreground window, independent of
    settings["window_filter"].
    """
    if not _WIN32_AVAILABLE:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid or None
    except Exception:
        return None


def _warn_presentmon_missing_once(path: Path):
    global _pm_missing_warned
    if _pm_missing_warned:
        return
    _pm_missing_warned = True
    try:
        _logger.warning("PresentMon.exe not found at %s — FPS tracking disabled for this session", path)
    except Exception:
        pass


def _warn_presentmon_launch_failed_once(exc: Exception):
    global _pm_launch_failed_warned
    if _pm_launch_failed_warned:
        return
    _pm_launch_failed_warned = True
    try:
        _logger.warning("Failed to launch PresentMon.exe", exc_info=exc)
    except Exception:
        pass


class _FpsTracker:
    """Owns one PresentMon subprocess targeting a single PID at a time.

    Reads its live stdout CSV stream on a background thread, keeps a rolling
    window of MsBetweenPresents samples, and exposes a smoothed FPS value.
    Fully self-contained: caller just calls start(pid, exe)/stop()/get_fps().
    """

    def __init__(self):
        self._lock          = threading.Lock()
        self._proc          = None
        self._reader_thread = None
        self._samples       = deque(maxlen=_FPS_ROLLING_WINDOW)
        self._last_sample_ts = 0.0

    def start(self, pid: int, exe_path: Path):
        self.stop()
        try:
            self._proc = subprocess.Popen(
                [str(exe_path), "--process_id", str(pid), "--output_stdout",
                 # Safety net: if a previous PresentMon child was ever hard-killed
                 # (crash, forced kill()) without tearing down its ETW trace
                 # session, the next launch would otherwise fail outright with
                 # "trace session ... already running". This makes launches
                 # self-healing regardless of how the last one ended.
                 "--stop_existing_session"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                # CREATE_NEW_PROCESS_GROUP is required for send_signal(CTRL_BREAK_EVENT)
                # in stop() below to target only this child, not our own process tree.
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._proc = None
            _warn_presentmon_launch_failed_once(exc)
            return
        with self._lock:
            self._samples.clear()
            self._last_sample_ts = 0.0
        self._reader_thread = threading.Thread(
            target=self._readLoop, daemon=True, name="PresentMonReader")
        self._reader_thread.start()

    def stop(self):
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                # Prefer a graceful CTRL_BREAK_EVENT over terminate()/kill():
                # PresentMon installs a console control handler that stops its
                # ETW trace session cleanly on break. TerminateProcess (what
                # Popen.terminate()/kill() use) skips that handler entirely and
                # can leave the ETW session orphaned, which then makes the
                # *next* launch fail until --stop_existing_session cleans it up
                # (confirmed empirically against the bundled binary — this is
                # a real, not theoretical, failure mode). Still bounded by the
                # short timeouts below so a stuck child never meaningfully
                # delays shutdown.
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        thread = self._reader_thread
        self._reader_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.3)
        if proc is not None:
            try:
                proc.wait(timeout=0.3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self._samples.clear()

    def get_fps(self):
        with self._lock:
            if not self._samples:
                return None
            if time.monotonic() - self._last_sample_ts > _FPS_STALE_SEC:
                return None
            avg_ms = sum(self._samples) / len(self._samples)
        if avg_ms <= 0:
            return None
        return 1000.0 / avg_ms

    def _readLoop(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        header_idx = None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if header_idx is None:
                    # Match case-insensitively — shipped PresentMon builds emit
                    # "msBetweenPresents" (lower-case "ms"), not the
                    # "MsBetweenPresents" capitalization used in some docs/older
                    # builds. Confirmed by running the actual bundled binary.
                    cols = [c.strip().lower() for c in line.split(",")]
                    header_idx = cols.index("msbetweenpresents") if "msbetweenpresents" in cols else -1
                    continue
                if header_idx < 0:
                    continue
                parts = line.split(",")
                if header_idx >= len(parts):
                    continue
                try:
                    ms = float(parts[header_idx])
                except ValueError:
                    continue
                if ms <= 0:
                    continue
                with self._lock:
                    self._samples.append(ms)
                    self._last_sample_ts = time.monotonic()
        except Exception:
            # Normal on terminate() (pipe closed mid-read) — not worth logging.
            pass


def _bootstrap():
    global _lhm_available, _Computer
    try:
        # Prefer persistent lib/ next to the exe (allows dropping in newer DLLs
        # without rebuilding).  Fall back to the bundled copy inside sys._MEIPASS.
        if getattr(sys, "frozen", False):
            _persistent = Path(sys.executable).parent / "lib"
            _bundled    = Path(sys._MEIPASS) / "lib"
            lib_dir = (_persistent
                       if (_persistent / "LibreHardwareMonitorLib.dll").exists()
                       else _bundled)
        else:
            lib_dir = Path(__file__).parent / "lib"
        if not (lib_dir / "LibreHardwareMonitorLib.dll").exists():
            return
        if str(lib_dir) not in sys.path:
            sys.path.insert(0, str(lib_dir))
        # Remove Zone.Identifier ADS on all DLLs — Windows blocks files downloaded
        # from the internet until unblocked. No-op if the stream doesn't exist.
        import ctypes
        for _dll in lib_dir.glob("*.dll"):
            ctypes.windll.kernel32.DeleteFileW(str(_dll) + ":Zone.Identifier")
        # Detect which .NET runtime the LHM DLL targets by scanning its binary.
        # net472 builds contain b'.NETFramework'; .NET 8/9/10 builds do not.
        _runtime = "netfx"
        try:
            _dll_bytes = (lib_dir / "LibreHardwareMonitorLib.dll").read_bytes()
            if b'.NETFramework' not in _dll_bytes:
                _runtime = "coreclr"
        except Exception:
            pass
        # pythonnet 3.x requires selecting the runtime before `import clr`.
        try:
            import pythonnet as _pn
            try:
                _pn.load(_runtime)
            except Exception:
                _pn.load("coreclr" if _runtime == "netfx" else "netfx")
        except (ImportError, AttributeError):
            pass                        # pythonnet 2.x — no load() needed
        import clr                      # pip install "pythonnet>=3.0.0"
        from System.Reflection import Assembly as _Asm
        # Load every support assembly by full file path, then the main lib last
        for _dll in sorted(lib_dir.glob("*.dll")):
            if _dll.stem == "LibreHardwareMonitorLib":
                continue
            try:
                _Asm.LoadFrom(str(_dll))
            except Exception:
                pass
        _Asm.LoadFrom(str(lib_dir / "LibreHardwareMonitorLib.dll"))
        from LibreHardwareMonitor.Hardware import Computer
        _Computer      = Computer
        _lhm_available = True
    except Exception:
        pass


_bootstrap()


def lhm_available() -> bool:
    return _lhm_available


class StatsPoller:
    """
    Background daemon thread that polls hardware stats each configured interval.

    Call setCallback(fn) before start(); fn(data: dict) is called on each poll.

    Data dict keys (all float, all optional — only present when sensor found):
        cpu_usage       %
        cpu_temp        °C
        gpu_usage       %
        gpu_temp        °C
        gpu_vram_used   GB
        gpu_vram_total  GB
        ram_used        GB
        ram_total       GB
        fps             frames/sec of the current foreground window (PresentMon),
                        smoothed over the last _FPS_ROLLING_WINDOW frames. Only
                        present when settings["stats"]["show_fps"] is enabled AND
                        PresentMon actually reported a live D3D/DXGI swap chain.
                        NOTE: since fps piggybacks on this same poll loop/thread,
                        it only runs while the loop runs — i.e. only when LHM is
                        available (same fallback as every other metric here).
    """

    def __init__(self, settings: dict):
        self._settings = settings
        self._lock     = threading.Lock()
        self._running  = False
        self._thread   = None
        self._callback = None
        self.latest: dict = {}
        # FPS/PresentMon tracking state — all owned exclusively by the poll
        # thread (created/torn down inside _pollLoop, never touched from
        # another thread), so no extra locking needed beyond self._lock for
        # publishing the resulting `latest` dict.
        self._fps_tracker       = None
        self._fps_target_pid    = None
        self._fps_pending_pid   = None
        self._fps_pending_since = 0.0

    def setCallback(self, cb):
        self._callback = cb

    def updateSettings(self, settings: dict):
        self._settings = settings

    def start(self):
        if not _lhm_available:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._pollLoop, daemon=True, name="StatsPoller")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ------------------------------------------------------------------

    def _interval(self) -> float:
        hz = self._settings.get("stats", {}).get("update_rate_hz", 1)
        return 1.0 / max(0.5, min(5.0, float(hz)))

    def _pollLoop(self):
        try:
            comp = _Computer()
            # Skip Ring0.Open() entirely (LHM >= 0.9.5, see Computer.IsRing0Enabled).
            # Ring0 is what extracts/installs the bundled WinRing0 kernel driver on
            # first use — Microsoft's Vulnerable Driver Blocklist flags and quarantines
            # it (VulnerableDriver:WinNT/Winring0). Disabling it means no driver service
            # is ever created; the tradeoff is losing sensors that need raw MSR/PCI
            # access (e.g. some CPU temperature sensors) on hardware whose vendor-level
            # sensors don't already cover that data through another path.
            comp.IsRing0Enabled  = False
            comp.IsCpuEnabled    = True
            comp.IsGpuEnabled    = True
            comp.IsMemoryEnabled = True
            comp.Open()
        except Exception:
            return
        try:
            while self._running:
                data = {}
                try:
                    for hw in comp.Hardware:
                        hw.Update()
                        self._harvest(hw, data)
                    if "ram_used" in data and "ram_available" in data:
                        data["ram_total"] = data["ram_used"] + data["ram_available"]
                except Exception:
                    pass

                self._updateFps(data)

                with self._lock:
                    self.latest = data

                if self._callback and data:
                    try:
                        self._callback(data)
                    except Exception:
                        pass

                time.sleep(self._interval())
        finally:
            if self._fps_tracker is not None:
                self._fps_tracker.stop()
                self._fps_tracker = None
            try:
                comp.Close()
            except Exception:
                pass

    def _updateFps(self, data: dict):
        """Track FPS of whichever window currently has OS focus via PresentMon.
        Deliberately ignores settings["window_filter"] — see module docstring.
        Owned entirely by the poll thread; no locking needed here."""
        try:
            fps_enabled = bool(self._settings.get("stats", {}).get("show_fps", False))
        except Exception:
            fps_enabled = False

        if not fps_enabled:
            if self._fps_tracker is not None:
                self._fps_tracker.stop()
                self._fps_tracker = None
                self._fps_target_pid  = None
                self._fps_pending_pid = None
            return

        cur_pid = _foreground_pid()
        if cur_pid is not None and cur_pid != self._fps_target_pid:
            if cur_pid == self._fps_pending_pid:
                if time.monotonic() - self._fps_pending_since >= _PID_DEBOUNCE_SEC:
                    self._retargetFps(cur_pid)
                    self._fps_pending_pid = None
            else:
                self._fps_pending_pid   = cur_pid
                self._fps_pending_since = time.monotonic()
        elif cur_pid == self._fps_target_pid:
            self._fps_pending_pid = None

        if self._fps_tracker is not None:
            fps_val = self._fps_tracker.get_fps()
            if fps_val is not None:
                data["fps"] = fps_val

    def _retargetFps(self, pid: int):
        exe_path = _presentmon_path()
        if not exe_path.exists():
            _warn_presentmon_missing_once(exe_path)
            # Don't retry every debounce window once we know the binary is
            # missing — just remember this pid as "targeted" (a no-op tracker
            # state) so we don't spam the missing-file check every poll tick.
            self._fps_target_pid = pid
            return
        if self._fps_tracker is None:
            self._fps_tracker = _FpsTracker()
        self._fps_tracker.start(pid, exe_path)
        self._fps_target_pid = pid

    def _harvest(self, hw, data: dict):
        hw_type = str(hw.HardwareType)

        if "Cpu" in hw_type:
            loads, temps = [], []
            for s in hw.Sensors:
                v = s.Value
                if v is None:
                    continue
                st = str(s.SensorType)
                if "Load" in st:
                    loads.append((s.Name, float(v)))
                elif "Temperature" in st and float(v) > 0.0:
                    # Skip 0.0 — LHM uses it as a sentinel when the sensor
                    # can't be read (e.g. AMD Zen 4 in current LHM versions)
                    temps.append((s.Name, float(v)))
            # Prefer "Total" load; fall back to first sensor
            cpu_load = next(
                (v for n, v in loads if "Total" in n),
                loads[0][1] if loads else None)
            # Priority: Package (Intel) → Tctl/Tdie or Tdie (AMD) → Average → first
            # Use explicit None checks — `or` would treat a valid 0.x value as falsy
            _temp_preds = [
                lambda n: "Package" in n,
                lambda n: "Tctl" in n or "Tdie" in n,
                lambda n: "Average" in n,
            ]
            cpu_temp = None
            for _pred in _temp_preds:
                _match = next((v for n, v in temps if _pred(n)), None)
                if _match is not None:
                    cpu_temp = _match
                    break
            if cpu_temp is None and temps:
                cpu_temp = temps[0][1]
            if cpu_load is not None and "cpu_usage" not in data:
                data["cpu_usage"] = cpu_load
            if cpu_temp is not None and "cpu_temp" not in data:
                data["cpu_temp"]  = cpu_temp

        elif "Gpu" in hw_type:
            loads, temps       = [], []
            vram_used_mb, vram_total_mb = [], []
            vram_used_gb, vram_total_gb = [], []
            for s in hw.Sensors:
                v = s.Value
                if v is None:
                    continue
                st   = str(s.SensorType)
                name = s.Name
                if "Load" in st:
                    loads.append((name, float(v)))
                elif "Temperature" in st:
                    temps.append((name, float(v)))
                elif "SmallData" in st:             # MB (VRAM)
                    if "Memory" in name and "Used" in name:
                        vram_used_mb.append(float(v))
                    elif "Memory" in name and "Total" in name:
                        vram_total_mb.append(float(v))
                elif "Data" in st:                  # GB (VRAM or RAM)
                    if "Memory" in name and "Used" in name:
                        vram_used_gb.append(float(v))
                    elif "Memory" in name and "Total" in name:
                        vram_total_gb.append(float(v))

            gpu_load = next((v for n, v in loads if "Core" in n),
                            loads[0][1] if loads else None)
            gpu_temp = next((v for n, v in temps if "Core" in n),
                            temps[0][1] if temps else None)
            if gpu_load is not None and "gpu_usage" not in data:
                data["gpu_usage"] = gpu_load
            if gpu_temp is not None and "gpu_temp" not in data:
                data["gpu_temp"]  = gpu_temp

            # Prefer GB sensors (Data type); fall back to MB/1024
            vram_u = (vram_used_gb[0]  if vram_used_gb
                      else vram_used_mb[0]  / 1024 if vram_used_mb  else None)
            vram_t = (vram_total_gb[0] if vram_total_gb
                      else vram_total_mb[0] / 1024 if vram_total_mb else None)
            if vram_u is not None and "gpu_vram_used"  not in data:
                data["gpu_vram_used"]  = vram_u
            if vram_t is not None and "gpu_vram_total" not in data:
                data["gpu_vram_total"] = vram_t

        elif "Memory" in hw_type:
            if "Virtual" in str(hw.Name):
                return          # skip virtual memory (RAM + pagefile); use physical only
            for s in hw.Sensors:
                v = s.Value
                if v is None:
                    continue
                st   = str(s.SensorType)
                name = s.Name
                if "Data" in st:
                    if "Used" in name and "ram_used" not in data:
                        data["ram_used"] = float(v)
                    elif ("Available" in name or "Free" in name) \
                            and "ram_available" not in data:
                        data["ram_available"] = float(v)
