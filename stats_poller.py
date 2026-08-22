"""
StatsPoller — polls hardware stats via LibreHardwareMonitor on a background thread.

Requirements:
  pip install pythonnet
  lib/LibreHardwareMonitorLib.dll  (from github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)

Falls back gracefully if either is missing — stats simply won't appear.
"""
import sys
import threading
import time
from pathlib import Path

_lhm_available = False
_Computer      = None
_SensorType    = None


def _bootstrap():
    global _lhm_available, _Computer, _SensorType
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
        from LibreHardwareMonitor.Hardware import Computer, SensorType
        _Computer      = Computer
        _SensorType    = SensorType
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
    """

    def __init__(self, settings: dict):
        self._settings = settings
        self._lock     = threading.Lock()
        self._running  = False
        self._thread   = None
        self._callback = None
        self.latest: dict = {}

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

                with self._lock:
                    self.latest = data

                if self._callback and data:
                    try:
                        self._callback(data)
                    except Exception:
                        pass

                time.sleep(self._interval())
        finally:
            try:
                comp.Close()
            except Exception:
                pass

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
