"""The Agent Deck's readings on Windows (owner 2026-10-24: "add in the agent deck if possible" for the partner's Windows PC). agentdeck.py
reads Linux's /proc and /sys; here psutil (BSD-3) gives the same numbers in the same shapes, so the Deck page needs no changes. Only
agentdeck.py imports this, and only when it is not on Linux. Read only: the controls stay in agentdeck.act."""
import os
import platform
import time

import psutil

CLK = 100                                    # agentdeck.py's tick unit off Linux: CPU seconds x 100
DISK_FS = {"ntfs", "refs", "exfat", "fat32", "fat"}
STATUS = {psutil.STATUS_RUNNING: "R", psutil.STATUS_SLEEPING: "S", psutil.STATUS_STOPPED: "T", psutil.STATUS_ZOMBIE: "Z",
          psutil.STATUS_DISK_SLEEP: "D", psutil.STATUS_IDLE: "I", psutil.STATUS_DEAD: "X"}
ME_NAME = (os.environ.get("USERNAME") or "").lower()
MINE, OTHER = -1, -2                         # agentdeck.ME is -1 off Linux: "mine" means the signed-in user's processes
ATTRS = ["pid", "username", "cpu_times", "create_time", "memory_info"]   # the fast ones on Windows (hundredths of a second for ~380)
CMD_BUDGET_S = 0.8                           # command lines are slow on Windows (~8 ms each): read once per process, a little each refresh
FIRST = ("python", "pythonw", "ollama", "ollama app", "claude", "node", "docker", "uvicorn")   # whose command lines the Deck needs first
_cmds: dict = {}


def host() -> tuple[str, str]:
    return platform.node(), f"Windows {platform.release()} ({platform.version()})"


def boot() -> float:
    return psutil.boot_time()


def uptime() -> float:
    return max(0.0, time.time() - boot())


def cpu_lines() -> dict:
    """{"cpu": (total, idle), "cpu0": ...}: the same ratios as /proc/stat (in seconds here, which the percentages don't mind)."""
    total = psutil.cpu_times()
    out = {"cpu": (sum(total), total.idle)}
    for i, t in enumerate(psutil.cpu_times(percpu=True)):
        out[f"cpu{i}"] = (sum(t), t.idle)
    return out


def meminfo() -> dict:
    v, s = psutil.virtual_memory(), psutil.swap_memory()
    return {"MemTotal": v.total, "MemAvailable": v.available, "MemFree": v.free, "Cached": 0, "SReclaimable": 0, "Buffers": 0, "Shmem": 0,
            "SwapTotal": s.total, "SwapFree": s.free}


def disks() -> list[dict]:
    out = []
    for p in psutil.disk_partitions(all=False):
        if (p.fstype or "").lower() not in DISK_FS:
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:                          # an empty card reader or a locked drive
            continue
        out.append({"mount": p.mountpoint, "device": p.device, "fs": p.fstype, "total": u.total, "used": u.used, "free": u.free})
    return out


def net() -> dict:
    return {n: (c.bytes_recv, c.bytes_sent) for n, c in psutil.net_io_counters(pernic=True).items() if "loopback" not in n.lower()}


def loadavg() -> list[float]:
    try:
        return list(psutil.getloadavg())         # Windows: psutil keeps its own average (zeros for the first few seconds)
    except (AttributeError, OSError):
        return []


def _name(n: str) -> str:
    n = (n or "").strip()
    return n[:-4] if n.lower().endswith(".exe") else n


def _snapshot() -> dict:
    """{pid: (parent pid, name, threads)} from one Toolhelp snapshot. psutil asks Windows for these once per process, and each ask walks
    the whole process list (about 3 s for 380 processes on the partner's PC, 2026-10-24); the snapshot is one walk."""
    if os.name != "nt":                                     # the tests on Linux: psutil is quick there
        out = {}
        for p in psutil.process_iter(["pid", "ppid", "name", "num_threads"], ad_value=None):
            out[p.info["pid"]] = (p.info.get("ppid") or 0, _name(p.info.get("name")), p.info.get("num_threads") or 0)
        return out
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)                        # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out, e = {}, Entry()
    e.dwSize = ctypes.sizeof(Entry)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), _name(e.szExeFile), int(e.cntThreads))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def procs() -> dict:
    """{pid: process} with agentdeck._proc's keys. Other users' and system processes show with what Windows lets a user see."""
    b, snap, out, handles = boot(), _snapshot(), {}, {}
    for p in psutil.process_iter(ATTRS, ad_value=None):
        i = p.info
        if not i.get("pid"):
            continue                             # System Idle Process
        ppid, name, threads = snap.get(i["pid"], (0, "", 0))
        ct, mem, who = i.get("cpu_times"), i.get("memory_info"), (i.get("username") or "")
        start = max(0.0, (i.get("create_time") or b) - b)
        handles[i["pid"]] = (p, (i["pid"], i.get("create_time")))
        out[i["pid"]] = {"pid": i["pid"], "ppid": ppid, "name": name, "state": "S",
                         "uid": MINE if ME_NAME and who.lower().split("\\")[-1] == ME_NAME else OTHER, "username": who.split("\\")[-1],
                         "cpu_ticks": int(((ct.user + ct.system) if ct else 0) * CLK), "nice": 0, "threads": threads,
                         "start": start, "virt": getattr(mem, "vms", 0) if mem else 0, "rss": getattr(mem, "rss", 0) if mem else 0,
                         "cmd": "", "rd": None, "wr": None, "kthread": False}
    until = time.time() + CMD_BUDGET_S
    for pid in sorted(out, key=lambda q: (out[q]["name"].lower() not in FIRST, q)):
        p, key = handles[pid]
        if key not in _cmds and time.time() < until:
            try:
                _cmds[key] = " ".join(p.cmdline())
            except (psutil.Error, OSError):
                _cmds[key] = ""
        out[pid]["cmd"] = _cmds.get(key) or out[pid]["name"]
    live = {h[1] for h in handles.values()}
    for key in [k for k in _cmds if k not in live]:             # ended processes
        del _cmds[key]
    return out


def comm(pid: int) -> str:
    try:
        return _name(psutil.Process(pid).name())
    except (psutil.Error, ValueError):
        return ""


def find(procs_: dict, *needles: str) -> int:
    """The first process whose command line has every needle (Rosie, Ollama: Windows has no systemctl to ask)."""
    for p in sorted(procs_.values(), key=lambda p: p["start"]):
        cmd = p["cmd"].lower()
        if all(n in cmd for n in needles):
            return p["pid"]
    return 0


def proc_info(pid: int) -> dict:
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            info = {"pid": pid, "ppid": p.ppid(), "name": _name(p.name()), "status": p.status(), "threads": p.num_threads(), "nice": 0,
                    "rss": p.memory_info().rss, "virt": p.memory_info().vms, "started": p.create_time(), "user": (p.username() or "").split("\\")[-1]}
            for k, f in (("cmd", lambda: " ".join(p.cmdline())), ("cwd", p.cwd), ("exe", p.exe),
                         ("fds", lambda: p.num_handles() if hasattr(p, "num_handles") else p.num_fds())):
                try:
                    info[k] = f()
                except (psutil.Error, OSError):
                    pass
        chain = []
        for q in p.parents()[:12]:
            chain.append({"pid": q.pid, "name": _name(q.name())})
        info["parents"] = chain
        info["children"] = [{"pid": c.pid, "name": _name(c.name())} for c in p.children()[:50]]
        return info
    except psutil.NoSuchProcess:
        return {"error": "That process has ended."}
    except psutil.AccessDenied:
        return {"error": "Windows doesn't let this user look inside that process."}
