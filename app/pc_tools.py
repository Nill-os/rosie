"""Full PC control tools (runs as your user, no sudo). Every call is logged to data/audit.log."""
import datetime as dt
import json
import subprocess
from pathlib import Path

import os
import shutil

def gui_env() -> dict:
    """Environment of the user's live desktop session (DISPLAY, XAUTHORITY, ...), read from the running
    desktop shell, because this service runs headless. Works for X11 and Wayland."""
    me = os.getuid()
    for name in ("gnome-shell", "gnome-session-binary", "cinnamon", "plasmashell", "xfce4-session"):
        r = subprocess.run(["pgrep", "-u", str(me), "-x", name], capture_output=True, text=True)
        for pid in r.stdout.split():
            try:
                raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            except OSError:
                continue
            env = dict(x.decode(errors="replace").split("=", 1) for x in raw if b"=" in x)
            keep = {k: env[k] for k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                                        "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE") if k in env}
            if "DISPLAY" in keep or "WAYLAND_DISPLAY" in keep:
                keep.setdefault("XDG_RUNTIME_DIR", f"/run/user/{me}")
                keep.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{me}/bus")
                return keep
    return {}


LOG = Path(__file__).resolve().parent.parent / "data" / "audit.log"
MAX_OUT = 2500


def _log(kind, detail):
    import testguard
    testguard.check(LOG)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{kind}\t{json.dumps(detail)[:2000]}\n")


def _trim(s):
    s = s or ""
    return s if len(s) <= MAX_OUT else s[:MAX_OUT // 2] + f"\n...[{len(s) - MAX_OUT} chars cut]...\n" + s[-MAX_OUT // 2:]


def run_command(command: str, cwd: str = "~", timeout: int = 60):
    cwd = str(Path(cwd).expanduser())
    _log("run", {"command": command, "cwd": cwd})
    try:
        p = subprocess.run(["bash", "-lc", command], cwd=cwd, capture_output=True, text=True,
                           timeout=min(int(timeout), 600), stdin=subprocess.DEVNULL, env={**os.environ, **gui_env()})
        return {"exit_code": p.returncode, "stdout": _trim(p.stdout), "stderr": _trim(p.stderr)}
    except subprocess.TimeoutExpired:
        return {"error": f"Timed out after {timeout}s"}


def read_file(path: str, max_chars: int = 6000):
    p = Path(path).expanduser()
    _log("read", {"path": str(p)})
    try:
        data = p.read_text(errors="replace")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"path": str(p), "total_chars": len(data), "content": data[:min(int(max_chars), 12000)]}


def write_file(path: str, content: str, append: bool = False):
    p = Path(path).expanduser()
    _log("write", {"path": str(p), "append": append, "chars": len(content)})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if p.exists() and not append:  # cheap safety net, no prompt
            backup = p.with_name(p.name + ".bak")
            backup.write_bytes(p.read_bytes())
        with p.open("a" if append else "w") as f:
            f.write(content)
        return {"status": "done", "path": str(p), "backup": str(backup) if backup else None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def list_dir(path: str = "~"):
    p = Path(path).expanduser()
    _log("list", {"path": str(p)})
    try:
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:200]
        return {"path": str(p), "items": [x.name + ("/" if x.is_dir() else "") for x in items]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def open_on_desktop(target: str):
    """Open a file, folder, URL or app on the user's screen (like double-clicking it)."""
    t = target if "://" in target else str(Path(target).expanduser())
    _log("open", {"target": t})
    if "://" not in t and not Path(t).exists():
        return {"error": f"Not found: {t}"}
    env = gui_env()
    if not env:
        return {"error": "Could not find your desktop session (is anyone logged in on screen?)."}
    try:
        p = subprocess.run(["xdg-open", t], env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"status": "launched (still starting)", "target": t}   # slow apps keep xdg-open alive; not a failure
    if p.returncode != 0:
        return {"error": f"xdg-open failed (code {p.returncode}): {(p.stderr or p.stdout).strip()[:200]}"}
    return {"status": "launched; check your screen. I cannot see the screen to confirm a window appeared.", "target": t}
