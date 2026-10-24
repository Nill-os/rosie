"""Agent Deck: one live view of everything the owner's agents do on this PC, with controls (owner 2026-10-21: "a program on my desktop
that shows a live view of everything my agents are doing and their ram, cpu, token etc use and allow controls ... see everything
neohtop can", then "add the agent deck into rosie").

Standard library only, so both Rosie (agentdeck_api.py, /api/deck/*, behind her login) and the desktop window (~/agent-deck, which
imports this file from Rosie's live slot) use the same code. The page is static/deck/.

What it reads (never writes, except through the controls): /proc and /sys (CPU, memory, disks, network, every process), nvidia-smi,
Claude Code's session files and transcripts (token counts, chat titles, latest step), the agent board, Rosie's config and corner
database, Ollama's /api/ps, docker inspect and systemctl. Controls stop processes by PID only (never by name), post on the board as
"owner", and are off in the test copy (ROSIE_TESTING / ROSIE_SANDBOX)."""
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent                    # the Rosie slot this file is in
HOME = Path.home()
OPS = Path(os.environ.get("ROSIE_OPS_DIR") or HOME / "rosie-ops")
CLAUDE = HOME / ".claude"
OLLAMA = "http://127.0.0.1:11434"
ROSIE_URL = "http://127.0.0.1:8765"
CONTAINERS = ("agent-zero", "rosie-graph")
A0_CHATS = HOME / "agent-zero" / "usr" / "chats"            # Agent Zero's chats on the owner's PC (root's files, but their times are readable)
USER_UNITS = ("assistant-dictation", "assistant-overlay", "graphiti-mcp")
LINUX = sys.platform.startswith("linux")                    # it reads /proc and /sys: on Windows the Deck is off (agentdeck_api says so)
CLK = os.sysconf("SC_CLK_TCK") if LINUX else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if LINUX else 4096
NCPU = os.cpu_count() or 1
ME = os.getuid() if LINUX else -1
MY_PID = os.getpid()
REAL_FS = {"ext4", "ext3", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "f2fs", "zfs", "fuseblk"}
STATES = {"R": "Running", "S": "Sleeping", "D": "Disk wait", "Z": "Zombie", "T": "Stopped", "t": "Traced", "I": "Idle", "X": "Dead"}
sys.path.insert(0, str(ROOT / "app"))
import agentdeck_tokens as tokens  # noqa: E402

WIN = None                                                   # off Linux, psutil gives the same readings (agentdeck_win.py, owner 2026-10-24)
if not LINUX:
    try:
        import agentdeck_win as WIN  # noqa: E402
    except ImportError:                                      # psutil missing: the Deck stays off, as before
        WIN = None
AVAILABLE = LINUX or WIN is not None

_lock = threading.Lock()
_prev = {"t": 0.0, "procs": {}, "cores": None, "net": None}
_cache = {}
_users = {}
_counter = tokens.TokenCounter(str(CLAUDE / "projects"))
_tok_lock = threading.Lock()
_tok_thread = {"t": None}


def start_counting():
    """The token reader starts with the first look at the deck, not with Rosie: nothing runs while nobody watches."""
    with _tok_lock:
        if _tok_thread["t"] is None:
            _tok_thread["t"] = threading.Thread(target=_count_tokens, daemon=True, name="agentdeck-tokens")
            _tok_thread["t"].start()


def _count_tokens():
    """Reads new transcript lines every few seconds in the background (the first read of a month of transcripts takes a few seconds)."""
    while True:
        try:
            fresh = tokens.TokenCounter(str(CLAUDE / "projects")) if not _counter.files else None
            if fresh:
                fresh.refresh()
                with _tok_lock:
                    _counter.files, _counter.activity, _counter.titles = fresh.files, fresh.activity, fresh.titles
            else:
                with _tok_lock:
                    _counter.refresh()
        except Exception:
            pass
        time.sleep(5)


def cached(key, ttl, fn):
    """fn() at most once per ttl seconds; a failure gives None (the page shows the part as unknown)."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _cache[key] = (time.time(), val)
    return val


def read(path, default=""):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def _env() -> dict:
    """Rosie runs as a system service: systemctl --user needs the owner's user bus spelled out."""
    rt = f"/run/user/{ME}"
    return {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR") or rt,
            "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={rt}/bus"}


def run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env())
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def user(uid):
    if uid not in _users:
        try:
            import pwd
            _users[uid] = pwd.getpwuid(uid).pw_name
        except (KeyError, ImportError):
            _users[uid] = str(uid)
    return _users[uid]


def live_slot() -> Path:
    return ROOT


# ---------------------------------------------------------------- system
def _cpu_lines():
    if WIN:
        return WIN.cpu_lines()
    out = {}
    for line in read("/proc/stat").splitlines():
        if line.startswith("cpu"):
            name, *vals = line.split()
            v = [int(x) for x in vals[:8]]
            out[name] = (sum(v), v[3] + v[4])                    # total, idle + iowait
    return out


def _meminfo():
    if WIN:
        return WIN.meminfo()
    m = {}
    for line in read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        m[k] = int(v.split()[0]) * 1024 if v.split() else 0
    return m


def _disks():
    if WIN:
        return WIN.disks()
    out, seen = [], set()
    for line in read("/proc/mounts").splitlines():
        dev, mnt, fs = (line.split() + ["", "", ""])[:3]
        mnt = mnt.replace("\\040", " ")
        if fs not in REAL_FS or dev in seen or mnt.startswith(("/snap", "/var/snap", "/boot/efi")):
            continue
        seen.add(dev)
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        out.append({"mount": mnt, "device": dev, "fs": fs, "total": total, "used": total - st.f_bfree * st.f_frsize, "free": free})
    return out


def _net():
    if WIN:
        return WIN.net()
    out = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        name, v = name.strip(), rest.split()
        if name != "lo" and len(v) >= 9:
            out[name] = (int(v[0]), int(v[8]))
    return out


def _gpu():
    q = run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed",
             "--format=csv,noheader,nounits"], 4)
    if not q.strip():
        return None
    f = [x.strip() for x in q.splitlines()[0].split(",")]
    num = lambda s: float(s) if re.fullmatch(r"[\d.]+", s or "") else None
    apps = {}
    for line in run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], 4).splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 2 and p[0].isdigit():
            apps[int(p[0])] = (num(p[1]) or 0) * 1024 * 1024
    return {"name": f[0], "util": num(f[1]), "mem_used": (num(f[2]) or 0) * 1024 * 1024, "mem_total": (num(f[3]) or 0) * 1024 * 1024,
            "temp": num(f[4]), "power": num(f[5]), "power_limit": num(f[6]), "fan": num(f[7]) if len(f) > 7 else None, "apps": apps}


def _cpu_temp():
    if WIN:
        return None                                      # Windows doesn't tell a user program the CPU temperature
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        if read(hw / "name").strip() in ("k10temp", "coretemp", "zenpower"):
            t = read(hw / "temp1_input").strip()
            if t.isdigit():
                return int(t) / 1000
    return None


# ---------------------------------------------------------------- processes
def _proc(pid: int):
    d = f"/proc/{pid}"
    try:
        stat = Path(d, "stat").read_text()
        uid = os.stat(d).st_uid
    except OSError:
        return None
    lp, rp = stat.find("("), stat.rfind(")")
    comm, f = stat[lp + 1:rp], stat[rp + 2:].split()
    try:
        cmd = Path(d, "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        cmd = ""
    rd = wr = None
    if uid == ME:
        for line in read(f"{d}/io").splitlines():
            if line.startswith("read_bytes:"):
                rd = int(line.split()[1])
            elif line.startswith("write_bytes:"):
                wr = int(line.split()[1])
    return {"pid": pid, "ppid": int(f[1]), "name": comm, "state": f[0], "uid": uid, "cpu_ticks": int(f[11]) + int(f[12]),
            "nice": int(f[16]), "threads": int(f[17]), "start": int(f[19]) / CLK, "virt": int(f[20]), "rss": int(f[21]) * PAGE,
            "cmd": cmd, "rd": rd, "wr": wr, "kthread": not cmd and int(f[1]) in (0, 2)}


def sample():
    """Everything that needs a time delta (CPU per core and per process, network, disk I/O), one sample per call."""
    with _lock:
        now = time.time()
        dt = max(now - _prev["t"], 0.05) if _prev["t"] else None
        if WIN:
            uptime, procs = WIN.uptime(), WIN.procs()
        else:
            uptime = float(read("/proc/uptime", "0 0").split()[0])
            procs = {}
            try:
                pids = os.listdir("/proc")
            except OSError:                              # no /proc and no psutil: nothing to list
                pids = []
            for name in pids:
                if name.isdigit():
                    p = _proc(int(name))
                    if p:
                        procs[p["pid"]] = p
        old = _prev["procs"]
        for p in procs.values():
            o = old.get(p["pid"])
            same = o and o["start"] == p["start"]
            cores = (p["cpu_ticks"] - o["cpu_ticks"]) / CLK / dt if same and dt else 0.0      # how many cores it kept busy
            p["cores_s"] = (cores + o["cores_s"]) / 2 if same and "cores_s" in o else cores    # smoothed: models work in bursts
            p["cores"] = round(p["cores_s"], 2)
            p["cpu"] = round(100 * p["cores_s"] / NCPU, 1)    # % of the whole CPU, the same scale as the header and System
            p["rd_rate"] = (p["rd"] - o["rd"]) / dt if same and dt and p["rd"] is not None and o["rd"] is not None else None
            p["wr_rate"] = (p["wr"] - o["wr"]) / dt if same and dt and p["wr"] is not None and o["wr"] is not None else None
            p["age"] = max(0.0, uptime - p["start"])
            p["user"] = p.get("username") or user(p["uid"])
        cores_now = _cpu_lines()
        cores = {}
        if _prev["cores"]:
            for k, (tot, idle) in cores_now.items():
                ot, oi = _prev["cores"].get(k, (tot, idle))
                cores[k] = round(100 * (1 - (idle - oi) / (tot - ot)), 1) if tot > ot else 0.0
        net_now = _net()
        net = []
        for k, (rx, tx) in net_now.items():
            o = (_prev["net"] or {}).get(k)
            net.append({"name": k, "rx": rx, "tx": tx, "rx_rate": (rx - o[0]) / dt if o and dt else 0, "tx_rate": (tx - o[1]) / dt if o and dt else 0})
        _prev.update(t=now, procs=procs, cores=cores_now, net=net_now)
    return procs, cores, net, uptime


def tree(procs, root: int) -> list[int]:
    kids = {}
    for p in procs.values():
        kids.setdefault(p["ppid"], []).append(p["pid"])
    out, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid in procs and pid not in out:
            out.append(pid)
            todo += kids.get(pid, [])
    return out


def group(procs, pids, gpu=None) -> dict:
    ps = [procs[p] for p in pids if p in procs]
    vram = sum((gpu or {}).get("apps", {}).get(p["pid"], 0) for p in ps)
    return {"pids": [p["pid"] for p in ps], "cpu": round(sum(p["cpu"] for p in ps), 1), "cores": round(sum(p["cores"] for p in ps), 1),
            "rss": sum(p["rss"] for p in ps), "count": len(ps),
            "vram": vram, "procs": [{"pid": p["pid"], "name": p["name"], "cpu": p["cpu"], "cores": p["cores"], "rss": p["rss"], "cmd": p["cmd"][:200]}
                                    for p in sorted(ps, key=lambda p: -p["rss"])][:40]}


# ---------------------------------------------------------------- agents
def _board():
    db = live_slot() / "data" / "board.db"
    if not db.exists():
        return None
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    try:
        now = time.time()
        return {"sessions": [dict(r) for r in c.execute("SELECT * FROM sessions WHERE gone IS NULL ORDER BY seen DESC LIMIT 40")],
                "claims": [dict(r) for r in c.execute("SELECT * FROM claims WHERE until > ? ORDER BY t", (now,))],
                "posts": [dict(r) for r in c.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 40")]}
    finally:
        c.close()


def _unit_pid(unit, user_unit=False) -> int:
    out = run(["systemctl", *(["--user"] if user_unit else []), "show", "-p", "MainPID", "-p", "ActiveState", unit], 3)
    d = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return int(d.get("MainPID") or 0) if d.get("ActiveState") == "active" else 0


def board_full() -> dict:
    """The Agent board tab: every post still on the board (it keeps 14 days) plus older ones from its permanent copy (graph_posts),
    who is around, claims, and a display name for each Claude session (its chat title)."""
    db = live_slot() / "data" / "board.db"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    try:
        now = time.time()
        posts = [dict(r) for r in c.execute("SELECT id, t, agent, kind, text, to_agent FROM posts ORDER BY id DESC LIMIT 600")]
        have = {p["id"] for p in posts}
        try:
            old = [dict(r, id=r["post_id"]) for r in c.execute("SELECT post_id, t, agent, kind, text, to_agent FROM graph_posts ORDER BY post_id DESC LIMIT 600")]
        except sqlite3.Error:
            old = []
        posts += [dict(p, archived=True) for p in old if p["id"] not in have]
        posts.sort(key=lambda p: p["t"], reverse=True)
        sessions = [dict(r) for r in c.execute("SELECT * FROM sessions ORDER BY seen DESC LIMIT 60")]
        claims = [dict(r) for r in c.execute("SELECT * FROM claims WHERE until > ? ORDER BY t", (now,))]
    finally:
        c.close()
    names = {}
    with _tok_lock:
        for f in (CLAUDE / "sessions").glob("*.json"):
            try:
                s = json.loads(f.read_text())
                names[f"claude-{int(s['pid'])}"] = {"title": _counter.title(s.get("sessionId") or ""), "name": s.get("name") or "",
                                                     "status": s.get("status") or "", "alive": read(f"/proc/{int(s['pid'])}/comm").strip() == "claude"}
            except (OSError, ValueError, KeyError):
                continue
    for s in sessions:
        s["task"] = clean_task(s.get("task"))
    return {"posts": posts[:800], "sessions": sessions, "claims": claims, "names": names}


def _user_units() -> list[str]:
    out = run(["systemctl", "--user", "list-units", "--all", "--type=service", "--plain", "--no-legend"], 4)
    return [line.split()[0] for line in out.splitlines() if line.split()]


def _unit(unit: str) -> dict:
    out = run(["systemctl", "--user", "show", "-p", "ActiveState", "-p", "SubState", "-p", "Result", "-p", "ControlGroup",
               "-p", "ExecMainStartTimestamp", "-p", "InactiveEnterTimestamp", unit], 3)
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _cgroup_pids(cg: str) -> list[int]:
    pids = []
    if not cg:
        return pids
    for root, _, files in os.walk(f"/sys/fs/cgroup{cg}"):
        if "cgroup.procs" in files:
            pids += [int(x) for x in read(f"{root}/cgroup.procs").split() if x.isdigit()]
    return pids


def job_unit(agent: str):
    """The user service a board job runs as (hairclip-variants -> hairclip-variants.service, hairclip-night ->
    hairclip-night-route.service): the running one first, else the latest."""
    units = [u for u in (cached("user-units", 10, _user_units) or []) if u.startswith(agent)]
    if not units:
        return None
    infos = [(u, cached(f"unit:{u}", 4, lambda u=u: _unit(u)) or {}) for u in units]
    infos.sort(key=lambda x: (x[1].get("ActiveState") == "active", x[1].get("ExecMainStartTimestamp", "")), reverse=True)
    return infos[0]


def _a0_host():
    """Rosie's a0_host (which container and folder Agent Zero uses on this PC), or None when it can't be loaded (the desktop window)."""
    try:
        import a0_host
        return a0_host
    except Exception:
        return None


def _a0_container() -> str:
    h = _a0_host()
    try:
        return h.container() if h else "agent-zero"
    except Exception:
        return "agent-zero"


def _a0_chats() -> Path:
    h = _a0_host()
    try:
        return h.usr() / "chats" if h else A0_CHATS
    except Exception:
        return A0_CHATS


def _containers():
    """Status by the deck's own names: Agent Zero is always "agent-zero" here, whatever its container is called on this PC."""
    out = {}
    real = _a0_container()
    names = [real if c == "agent-zero" else c for c in CONTAINERS]
    raw = run(["docker", "inspect", *names], 5)
    for c in json.loads(raw) if raw.strip() else []:
        st = c.get("State") or {}
        name = c["Name"].lstrip("/")
        out["agent-zero" if name == real else name] = {"status": st.get("Status"), "pid": st.get("Pid") or 0, "started": st.get("StartedAt", "")[:19],
                                      "image": (c.get("Config") or {}).get("Image", "")}
    return out


def _http_json(url, timeout=1.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _a0_activity() -> dict:
    """When Agent Zero last wrote a chat (it saves the chat as it works) and which one. Its own CPU says little: the model runs in Ollama."""
    last, chat, n = 0.0, "", 0
    try:
        for d in _a0_chats().iterdir():
            if not d.is_dir():
                continue
            n += 1
            try:
                t = max((d / "chat.json").stat().st_mtime, d.stat().st_mtime)
            except OSError:
                t = d.stat().st_mtime
            if t > last:
                last, chat = t, d.name
    except OSError:
        pass
    return {"last_active": last, "chat": chat, "chats": n}


def _rosie_config():
    try:
        return json.loads(read(live_slot() / "data" / "config.json") or "{}")
    except ValueError:
        return {}


def _graph_done():
    db = live_slot() / "data" / "corner" / "corner.db"
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        done = c.execute("SELECT COUNT(*) FROM graph_items WHERE done_at IS NOT NULL AND kind != 'move'").fetchone()[0]
        day = c.execute("SELECT COUNT(*) FROM graph_items WHERE done_at >= ?", (time.strftime("%Y-%m-%d"),)).fetchone()[0]
        last = c.execute("SELECT MAX(done_at) FROM graph_items").fetchone()[0] or ""
        return {"done": done, "today": day, "last": last}
    finally:
        c.close()


def _orchestrator():
    try:
        return bool(json.loads(read(CLAUDE / "skills" / "orchestrate" / "state.json") or "{}").get("on"))
    except ValueError:
        return None


def agents(procs, gpu):
    board = cached("board", 2, _board) or {"sessions": [], "claims": [], "posts": []}
    tasks = {s["agent"]: s for s in board["sessions"]}
    with _tok_lock:
        tok = _counter.by_session()
        acts = dict(_counter.activity)
        titles = {sid: _counter.title(sid) for sid in list(_counter.titles)}
        today = _counter.by_day(time.localtime().tm_gmtoff // 60).get(time.strftime("%Y-%m-%d"), {})
    claude = []
    for f in sorted((CLAUDE / "sessions").glob("*.json")):
        try:
            s = json.loads(f.read_text())
            pid = int(s["pid"])
        except (OSError, ValueError, KeyError):
            continue
        if pid not in procs or procs[pid]["name"] != "claude":
            continue
        g = group(procs, tree(procs, pid), gpu)
        b = tasks.get(f"claude-{pid}") or {}
        claude.append({"pid": pid, "agent": f"claude-{pid}", "name": s.get("name") or "", "status": s.get("status") or "", "cwd": s.get("cwd") or "",
                       "session": s.get("sessionId") or "", "started": (s.get("startedAt") or 0) / 1000, "version": s.get("version") or "",
                       "task": clean_task(b.get("task")), "step": acts.get(s.get("sessionId") or ""), "title": titles.get(s.get("sessionId") or "", ""), "tokens": tok.get(s.get("sessionId") or "", {}),
                       "mcp": sum(1 for p in g["procs"] if " mcp" in p["cmd"] or "mcp_server" in p["cmd"] or "_mcp.py" in p["cmd"]), **g})
    cont = cached("docker", 5, _containers) or {}
    rpid = (WIN.find(procs, "server:app", "8765") if WIN else cached("rosie-pid", 5, lambda: _unit_pid("assistant.service"))) or 0
    health = cached("rosie-health", 3, lambda: _http_json(ROSIE_URL + "/healthz"))
    cfg = _rosie_config()
    units = {u: cached(f"unit-{u}", 5, lambda u=u: _unit_pid(u + ".service", True)) or 0 for u in USER_UNITS}
    rosie_pids = tree(procs, rpid) + sum((tree(procs, units[u]) for u in ("assistant-dictation", "assistant-overlay") if units[u]), [])
    rosie = {"up": bool(health), "slot": (health or {}).get("slot"), "commit": (health or {}).get("commit"), "stopped": (OPS / "stopped").exists(),
             "browsing": bool(cfg.get("corner_on")), "planning": bool(cfg.get("claude_planning")), "orchestrator": _orchestrator(),
             "brain": cfg.get("brain") or "", "model": cfg.get("chat_model") or "", "graph": cached("graph", 15, _graph_done),
             "plan": cached("plan", 10, lambda: json.loads(read(live_slot() / "data" / "claude_plan.json") or "{}")),
             "task": (tasks.get("rosie") or {}).get("task") or "", "seen": (tasks.get("rosie") or {}).get("seen") or 0, **group(procs, rosie_pids, gpu)}
    opid = (WIN.find(procs, "ollama", "serve") or WIN.find(procs, "ollama") if WIN else cached("ollama-pid", 5, lambda: _unit_pid("ollama.service"))) or 0
    ps = cached("ollama-ps", 2, lambda: _http_json(OLLAMA + "/api/ps")) or {}
    ollama = {"up": bool(opid), "models": [{"name": m.get("name"), "size": m.get("size"), "vram": m.get("size_vram"), "ctx": m.get("context_length"),
                                           "expires": m.get("expires_at", "")[:19], "params": (m.get("details") or {}).get("parameter_size")}
                                          for m in ps.get("models", [])], **group(procs, tree(procs, opid), gpu)}
    a0 = cont.get("agent-zero") or {}
    agent_zero = {"status": a0.get("status") or "missing", "started": a0.get("started"), "task": (tasks.get("agent-zero") or {}).get("task") or "",
                  "seen": (tasks.get("agent-zero") or {}).get("seen") or 0, **(cached("a0-activity", 2, _a0_activity) or {}),
                  **group(procs, tree(procs, a0.get("pid") or 0), gpu)}
    rg = cont.get("rosie-graph") or {}
    gpid = units.get("graphiti-mcp") or 0
    memory = {"status": rg.get("status") or "missing", "mcp": bool(gpid), **group(procs, tree(procs, rg.get("pid") or 0) + tree(procs, gpid), gpu)}
    known = {"rosie", "agent-zero", "owner"} | {c["agent"] for c in claude}       # board agents not in this list are "jobs"
    others = []
    seen = {s["agent"]: s for s in board["sessions"]}
    for c in board["claims"]:                                  # a job that only claims (like a nightly autoroute) still shows
        seen.setdefault(c["agent"], {"agent": c["agent"], "task": c.get("why") or "", "seen": c["t"], "cwd": ""})
    for s in seen.values():
        if s["agent"] in known or s["agent"].startswith("claude-"):
            continue
        cl = [c["thing"] for c in board["claims"] if c["agent"] == s["agent"]]
        if cl or time.time() - (s["seen"] or 0) < 6 * 3600:
            ju = job_unit(s["agent"])
            info = ju[1] if ju else {}
            pids = []
            if info.get("ActiveState") == "active":
                for p in _cgroup_pids(info.get("ControlGroup", "")):
                    pids += tree(procs, p)
            others.append({"agent": s["agent"], "task": s.get("task") or "", "seen": s["seen"], "claims": cl, "cwd": s.get("cwd") or "",
                           "unit": ju[0] if ju else "", "state": info.get("ActiveState", ""), "result": info.get("Result", ""),
                           "ended": info.get("InactiveEnterTimestamp", "") if info.get("ActiveState") != "active" else "",
                           **group(procs, list(dict.fromkeys(pids)), gpu)})
    seen_pids = set()
    for g in [*claude, rosie, ollama, agent_zero, memory, *others]:
        seen_pids.update(g["pids"])
    total = group(procs, list(seen_pids), gpu)                  # every agent process once, even when two groups share one
    return {"claude": claude, "rosie": rosie, "ollama": ollama, "agent_zero": agent_zero, "memory": memory, "others": others,
            "board": board, "tokens_today": today, "tokens_total": _sum(tok.values()),
            "total": {k: total[k] for k in ("cpu", "cores", "rss", "count", "vram")}}


def clean_task(t: str) -> str:
    """A session's latest prompt as its task; Claude Code's own notices (<task-notification> ...) become one plain line."""
    t = (t or "").strip()
    if t.startswith("<"):
        m = re.search(r"<summary>(.*?)</summary>", t, re.S)
        return "Background job: " + m.group(1).strip()[:150] if m else "(a notice from Claude Code)"
    return t


def _sum(rows):
    out = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "messages": 0}
    for r in rows:
        for k in out:
            out[k] += r.get(k, 0)
    return out


# ---------------------------------------------------------------- the snapshot
def snapshot():
    if not AVAILABLE:                                    # not Linux and no psutil: the page is hidden, this keeps a direct call from raising
        return {"error": "The Agent Deck needs Linux's /proc or the psutil package, and this system has neither."}
    start_counting()
    procs, cores, net, uptime = sample()
    gpu = cached("gpu", 2, _gpu)
    mem = _meminfo()
    ag = agents(procs, gpu)
    owner = {}
    for key, pids in [("rosie", ag["rosie"]["pids"]), ("ollama", ag["ollama"]["pids"]), ("agent-zero", ag["agent_zero"]["pids"]),
                      ("memory", ag["memory"]["pids"])] + [(f"claude {c['name'] or c['pid']}", c["pids"]) for c in ag["claude"]] \
            + [(j["agent"], j["pids"]) for j in ag["others"]]:
        for p in pids:
            owner.setdefault(p, key)
    plist = [{"pid": p["pid"], "ppid": p["ppid"], "name": p["name"], "status": STATES.get(p["state"], p["state"]), "user": p["user"],
              "cpu": p["cpu"], "cores": p["cores"], "rss": p["rss"], "virt": p["virt"], "threads": p["threads"], "nice": p["nice"], "age": round(p["age"]),
              "rd": p["rd_rate"], "wr": p["wr_rate"], "cmd": p["cmd"][:300], "kthread": p["kthread"], "agent": owner.get(p["pid"], ""),
              "vram": (gpu or {}).get("apps", {}).get(p["pid"], 0), "mine": p["uid"] == ME}
             for p in procs.values()]
    load = WIN.loadavg() if WIN else read("/proc/loadavg").split()
    if gpu:
        gpu = {k: v for k, v in gpu.items() if k != "apps"}
    hostname, kernel = WIN.host() if WIN else (os.uname().nodename, os.uname().release)
    return {"t": time.time(), "host": hostname, "kernel": kernel, "uptime": uptime,
            "load": [float(x) for x in load[:3]] if len(load) >= 3 else [], "cpu": {"total": cores.get("cpu", 0.0),
            "cores": [cores.get(f"cpu{i}", 0.0) for i in range(NCPU)], "temp": _cpu_temp()},
            "mem": {"total": mem.get("MemTotal", 0), "available": mem.get("MemAvailable", 0), "free": mem.get("MemFree", 0),
                    "cached": mem.get("Cached", 0) + mem.get("SReclaimable", 0), "buffers": mem.get("Buffers", 0), "shared": mem.get("Shmem", 0),
                    "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0)},
            "disks": cached("disks", 10, _disks) or [], "net": net, "gpu": gpu, "agents": ag, "procs": plist,
            "threads": sum(p["threads"] for p in plist)}


def proc_info(pid: int) -> dict:
    if WIN:
        return WIN.proc_info(pid)
    p = _proc(pid)
    if not p:
        return {"error": "That process has ended."}
    d = f"/proc/{pid}"
    info = {**{k: p[k] for k in ("pid", "ppid", "name", "nice", "threads", "rss", "virt", "cmd")}, "status": STATES.get(p["state"], p["state"]),
            "user": user(p["uid"]), "started": time.time() - (float(read("/proc/uptime", "0").split()[0]) - p["start"])}
    status = {k: v.strip() for k, _, v in (line.partition(":") for line in read(f"{d}/status").splitlines())}
    info.update({k: status.get(k, "") for k in ("VmPeak", "VmHWM", "VmSwap", "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches", "Cpus_allowed_list")})
    if p["uid"] == ME:
        for k, f in (("cwd", "cwd"), ("exe", "exe")):
            try:
                info[k] = os.readlink(f"{d}/{f}")
            except OSError:
                pass
        try:
            info["fds"] = len(os.listdir(f"{d}/fd"))
        except OSError:
            pass
        info["io"] = {k: v.strip() for k, _, v in (line.partition(":") for line in read(f"{d}/io").splitlines())}
    chain, q = [], p["ppid"]
    while q > 0 and len(chain) < 12:
        pp = _proc(q)
        if not pp:
            break
        chain.append({"pid": q, "name": pp["name"]})
        q = pp["ppid"]
    info["parents"] = chain
    info["children"] = [{"pid": c["pid"], "name": c["name"]} for c in (_proc(int(n)) for n in os.listdir("/proc") if n.isdigit()) if c and c["ppid"] == pid][:50]
    return info


# ---------------------------------------------------------------- controls
SIGNALS = {"TERM": signal.SIGTERM, "KILL": getattr(signal, "SIGKILL", signal.SIGTERM), "STOP": getattr(signal, "SIGSTOP", None),
           "CONT": getattr(signal, "SIGCONT", None)}


def _detached(cmd):
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=_env())


def _board_mod():
    app = str(live_slot() / "app")
    if app not in sys.path:
        sys.path.insert(0, app)
    import agentboard
    return agentboard


def _rosie_py(code: str) -> str:
    slot = live_slot()
    r = subprocess.run([str(slot / ".venv" / "bin" / "python"), "-c", code], cwd=slot / "app", capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["Rosie's settings could not be changed"])[-1][:200])
    return r.stdout.strip()


def controls_off() -> bool:
    """The test suite and the stage copy never stop processes, containers or Rosie, nor change settings."""
    return os.environ.get("ROSIE_TESTING") == "1" or bool(os.environ.get("ROSIE_SANDBOX"))


def act(body: dict) -> dict:
    a = body.get("action")
    if a == "kill" and int(body.get("pid") or 0) <= 1:
        return {"error": "That process can't be stopped from here."}
    if controls_off():
        return {"error": "Controls are off in this test copy of Rosie."}
    if a == "kill":
        pid, sig = int(body.get("pid") or 0), SIGNALS.get(str(body.get("signal") or "TERM").upper())
        if pid <= 1 or pid == MY_PID or pid == os.getppid() or not sig:
            return {"error": "That process can't be stopped from here."}
        os.kill(pid, sig)
        return {"ok": True, "msg": f"Sent {body.get('signal', 'TERM')} to {pid}."}
    if a == "ollama_unload":
        m = str(body.get("model") or "")
        req = urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps({"model": m, "keep_alive": 0}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        _cache.pop("ollama-ps", None)
        return {"ok": True, "msg": f"Unloaded {m}."}
    if a == "docker":
        name, op = body.get("name"), body.get("op")
        if name not in CONTAINERS or op not in ("start", "stop", "restart"):
            return {"error": "Unknown container or action."}
        r = subprocess.run(["docker", op, _a0_container() if name == "agent-zero" else name], capture_output=True, text=True, timeout=90, env=_env())
        _cache.pop("docker", None)
        return {"ok": True, "msg": f"{name}: {op} done."} if r.returncode == 0 else {"error": r.stderr.strip()[:200]}
    if a == "power":
        op = body.get("op")
        if op not in ("start", "stop"):
            return {"error": "Unknown action."}
        _detached([str(OPS / "power.sh"), op])
        return {"ok": True, "msg": "Starting everything…" if op == "start" else "Shutting everything down…"}
    if a == "open":
        _detached([str(OPS / "open.sh"), *(["agentzero"] if body.get("what") == "agentzero" else [])])
        return {"ok": True, "msg": "Opening…"}
    if a == "note":
        r = _board_mod().post(str(body.get("text") or ""), "note", str(body.get("to") or "all"), agent="owner")
        _cache.pop("board", None)
        return {"ok": True, "msg": "Note posted."} if r.get("ok") else {"error": r.get("error", "Not posted.")}
    if a == "release":
        r = _board_mod().release(str(body.get("thing") or ""), agent="owner", force=True)
        _cache.pop("board", None)
        return {"ok": True, "msg": "Claim ended."} if r.get("ok", True) and not r.get("error") else {"error": r.get("error")}
    if a == "claude_stop":
        pid = int(body.get("pid") or 0)
        s = read(CLAUDE / "sessions" / f"{pid}.json")
        if not s or (WIN.comm(pid) if WIN else read(f"/proc/{pid}/comm").strip()) != "claude":
            return {"error": "That Claude session is not running."}
        os.kill(pid, signal.SIGTERM)
        return {"ok": True, "msg": f"Asked Claude session {pid} to exit."}
    if a == "toggle":
        name, on = body.get("name"), bool(body.get("on"))
        if name == "orchestrator":
            r = subprocess.run([str(HOME / ".local" / "bin" / "delegate"), "on" if on else "off"], capture_output=True, text=True, timeout=10, env=_env())
            return {"ok": True, "msg": r.stdout.strip()} if r.returncode == 0 else {"error": r.stderr.strip()[:200]}
        if name == "browsing":
            _rosie_py(f"import corner; corner.save_settings({{'corner_on': {on}}})")
            return {"ok": True, "msg": "Rosie browses when idle." if on else "Rosie's browsing is off."}
        if name == "planning":
            _rosie_py(f"import config; c = config.load(); c['claude_planning'] = {on}; config.save(c)")
            return {"ok": True, "msg": "Rosie plans with Claude." if on else "Rosie plans on her own model."}
        return {"error": "Unknown switch."}
    return {"error": "Unknown action."}
