"""The PC's own health, for Rosie's background work (owner 2026-10-23: "cap rosies browsing ram when i am using the pc so i can run other
stuff while she is browsing also keep in mind my pc health throughout all of this. this is on a laptop").

Every reading is cheap, cached for a few seconds, and None when this PC can't tell (a desktop has no battery, Windows hides the CPU
temperature, a session without GNOME or X has no idle time); the rule that needs a missing reading is skipped. Works on Linux and Windows.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

CACHE_S = 10
_cache: dict = {}
WIN = sys.platform == "win32"
HWMON = Path("/sys/class/hwmon")
POWER = Path("/sys/class/power_supply")
CPU_CHIPS = ("k10temp", "coretemp", "zenpower", "cpu_thermal", "soc_thermal")   # the CPU's own sensor; acpitz only when none of these


def _cached(name: str, fn):
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        v = fn()
    except Exception:                                      # a sensor that breaks must never stop Rosie
        v = None
    _cache[name] = (time.monotonic(), v)
    return v


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- memory
def _mem_linux():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def _mem_windows():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    return int(m.ullAvailPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None


def mem_available(fresh: bool = False) -> int | None:
    """Bytes of memory other programs can still get without swapping."""
    if fresh:
        _cache.pop("mem", None)
    return _cached("mem", _mem_windows if WIN else _mem_linux)


# ---------------------------------------------------------------- CPU temperature
def _temp_linux():
    chips = {}
    for d in HWMON.glob("hwmon*"):
        temps = [int(t) / 1000 for f in d.glob("temp*_input") if (t := _read(f)).lstrip("-").isdigit()]
        if temps:
            chips.setdefault(_read(d / "name"), []).extend(temps)
    for name in CPU_CHIPS + ("acpitz",):
        if chips.get(name):
            return max(chips[name])
    return None


def cpu_temp() -> float | None:
    """The hottest CPU sensor in °C (Linux only: Windows needs admin rights to read it)."""
    return None if WIN else _cached("temp", _temp_linux)


# ---------------------------------------------------------------- battery
def _battery_linux():
    has_battery = False
    for d in POWER.glob("*"):
        kind = _read(d / "type")
        if kind in ("Mains", "USB") and _read(d / "online") == "1" and _read(d / "scope") != "Device":
            return False
        if kind == "Battery" and _read(d / "scope") != "Device":       # scope Device = a mouse or headset battery
            has_battery = True
    return True if has_battery else None


def _battery_windows():
    class PS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    p = PS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)) or p.BatteryFlag == 128 or p.ACLineStatus == 255:
        return None
    return p.ACLineStatus == 0


def on_battery() -> bool | None:
    """True on a laptop running off its battery; None on a desktop."""
    return _cached("battery", _battery_windows if WIN else _battery_linux)


# ---------------------------------------------------------------- keyboard and mouse
def _idle_linux():
    env = dict(os.environ)
    bus = Path(f"/run/user/{os.getuid()}/bus")
    if "DBUS_SESSION_BUS_ADDRESS" not in env and bus.exists():           # Rosie runs as a service, outside the desktop session
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    if shutil.which("gdbus"):
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor", "--object-path",
                            "/org/gnome/Mutter/IdleMonitor/Core", "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
                           capture_output=True, text=True, timeout=3, env=env)
        m = re.search(r"uint64 (\d+)", r.stdout)                         # "(uint64 6620,)" in milliseconds
        if r.returncode == 0 and m:
            return int(m.group(1)) / 1000
    if env.get("DISPLAY") and shutil.which("xprintidle"):
        r = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=3, env=env)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout) / 1000
    return None


def _idle_windows():
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]
    li = LII()
    li.cbSize = ctypes.sizeof(LII)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def input_idle_s() -> float | None:
    """Seconds since the last key press or mouse move on this PC."""
    return _cached("idle", _idle_windows if WIN else _idle_linux)


def disk_free(path) -> int:
    """Free bytes on the disk holding path (Linux and Windows). Raises OSError like os.statvfs did."""
    return shutil.disk_usage(path).free


def snapshot() -> dict:
    """For the Settings pages: what Rosie sees right now."""
    mem, idle = mem_available(), input_idle_s()
    return {"mem_free_gb": round(mem / 1024 ** 3, 1) if mem is not None else None, "cpu_temp": cpu_temp(), "on_battery": on_battery(),
            "idle_s": round(idle) if idle is not None else None}


if __name__ == "__main__":
    print(snapshot())
