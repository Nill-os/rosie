# board #772 owner-live-server-list
import datetime as dt
import ipaddress
import json
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import security_watch
import testguard

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "servers.json"
STALE_S = 6 * 3600
EPHEMERAL = 32768  # board #772 why-skip-ephemeral-ports
PORTS = {22: "SSH", 53: "DNS", 25: None, 111: None, 139: None, 445: "Files (SMB)", 2049: "Files (NFS)", 3128: None, 5432: None,
         3306: None, 6379: None, 9090: "Cockpit", 8006: "Proxmox"}
PROGRAMS = {"pveproxy": "Proxmox", "immich": "Immich", "navidrome": "Navidrome", "pihole-ftl": "Pi-hole", "jellyfin": "Jellyfin",
            "plexmediaserver": "Plex", "smbd": "Files (SMB)", "sshd": "SSH", "nginx": "Web server", "caddy": "Web server",
            "homeassistant": "Home Assistant", "hass": "Home Assistant", "grafana": "Grafana", "portainer": "Portainer"}
NOT_WEB = {22, 53, 445, 2049}
WEB_PATHS = {"pihole-ftl": "/admin/"}

DISCOVER_SH = r"""
PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
echo "MACHINE host $(hostname) running -"
ss -ltnpH 2>/dev/null | awk '{print "PORT host", $4, $6}'
if command -v pct >/dev/null; then
  pct list | awk 'NR>1 {print $1, $2}' | while read id st; do
    ip=$(lxc-info -n "$id" -iH 2>/dev/null | grep -v ':' | grep -v '^127\.' | head -1)
    [ -n "$ip" ] || ip=$(pct config "$id" | grep -o 'ip=[0-9.]*' | head -1 | cut -d= -f2)
    echo "MACHINE $id $(pct config "$id" | sed -n 's/^hostname: //p') $st ${ip:--}"
    [ "$st" = running ] && pct exec "$id" -- ss -ltnpH 2>/dev/null </dev/null | awk -v m="$id" '{print "PORT", m, $4, $6}'
  done
fi
true
"""

_lock = threading.Lock()
state = {"running": False, "last_error": ""}


def load() -> dict:
    try:
        d = json.loads(STATE.read_text())
    except (OSError, ValueError):
        d = {}
    for k, v in (("machines", []), ("manual", []), ("labels", {})):
        d.setdefault(k, v)
    return d


def save(d: dict):
    testguard.check(STATE)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(STATE)


def _host_address() -> str:
    # board #772 why-address-from-ssh
    cmd = security_watch._ssh_cmd() or []
    return cmd[-1].rpartition("@")[2] if cmd else ""


def parse(text: str, host_ip: str) -> list[dict]:
    machines, by_id = [], {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "MACHINE":
            mid, name, status, ip = f[1], f[2], f[3], f[4]
            m = {"id": mid, "name": name, "kind": "server" if mid == "host" else f"container {mid}",
                 "ip": host_ip if mid == "host" else ("" if ip == "-" else ip), "running": status == "running", "ports": []}
            machines.append(m)
            by_id[mid] = m
        elif len(f) >= 3 and f[0] == "PORT" and f[1] in by_id:
            local, prog = f[2], " ".join(f[3:])
            addr, _, port = local.rpartition(":")
            if not port.isdigit() or "%" in addr or addr.startswith(("127.", "[::1]")):
                continue
            p = int(port)
            m = by_id[f[1]]
            if p < EPHEMERAL and p not in {x for x, _ in m["ports"]}:
                name = re.search(r'\(\("([^"\s]+)', prog)
                m["ports"].append((p, name.group(1) if name else ""))
    for m in machines:
        m["ports"].sort()
    return machines


def _title(html: str) -> str:
    t = re.search(r"<title[^>]*>([^<]{1,80})</title>", html or "", re.I)
    return re.sub(r"\s+", " ", t.group(1)).strip() if t else ""


def probe(ip: str, port: int) -> dict:
    # board #772 why-ask-not-guess
    if port in NOT_WEB:
        return {"scheme": "", "title": ""}
    for scheme in ("http", "https"):
        try:
            r = httpx.get(f"{scheme}://{ip}:{port}/", timeout=4, verify=False, follow_redirects=True)
            if scheme == "http" and r.status_code == 400 and "https" in r.text.lower():
                continue
            return {"scheme": r.url.scheme if r.url.port in (None, port) else scheme, "title": _title(r.text[:20000])}
        except (httpx.HTTPError, OSError):
            continue
    return {"scheme": "", "title": ""}


def _program(name: str) -> str:
    n = name.lower()
    return next((v for k, v in PROGRAMS.items() if n == k or n.startswith(k + "-")), "")


def service(ip: str, port: int, program: str, web: dict) -> dict | None:
    known = PORTS.get(port, "")
    if known is None:
        return None
    prog = _program(program)
    scheme = web["scheme"] or ({80: "http", 443: "https"}.get(port, "") if prog else "")
    if scheme:
        label = (prog if prog not in ("", "SSH", "Files (SMB)", "Web server") else "") or web["title"] or known or program or f"Port {port}"
    else:
        label = known or prog or program or f"Port {port}"
    url = ""
    if scheme:
        url = f"{scheme}://{ip}" + ("" if (scheme, port) in (("http", 80), ("https", 443)) else f":{port}") + WEB_PATHS.get(program.lower(), "/")
    elif port == 445:
        url = f"smb://{ip}/"
    return {"port": port, "label": label, "program": program, "url": url, "hint": f"ssh root@{ip}" if port == 22 else ""}


def discover() -> dict:
    cmd = security_watch._ssh_cmd()
    if not cmd:
        raise RuntimeError("no home server is set up (Settings > Security)")
    text = security_watch._run(cmd + ["sh -s"], 180, DISCOVER_SH)
    machines = parse(text, _host_address())
    jobs = [(m, p, prog) for m in machines if m["ip"] and m["running"] for p, prog in m["ports"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        webs = list(ex.map(lambda j: probe(j[0]["ip"], j[1]), jobs))
    for m in machines:
        m["services"] = []
    for (m, p, prog), web in zip(jobs, webs):
        s = service(m["ip"], p, prog, web)
        if s:
            m["services"].append(s)
    for m in machines:
        del m["ports"]
        progs = {}
        for s in m["services"]:  # board #772 why-keep-https-one
            if s["url"] and s["program"]:
                progs.setdefault(s["program"], []).append(s)
        for same in progs.values():
            if len(same) > 1 and any(x["url"].startswith("https") for x in same):
                m["services"] = [x for x in m["services"] if x not in same or x["url"].startswith("https")][:]
    d = load()
    d.update(machines=machines, found_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    save(d)
    return d


def refresh() -> bool:
    with _lock:
        if state["running"]:
            return False
        state["running"] = True

    def run():
        try:
            discover()
            state["last_error"] = ""
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            state["running"] = False
    threading.Thread(target=run, daemon=True, name="servers").start()
    return True


def _up(ip: str, port: int) -> float | None:
    t = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=1.5):
            return round((time.monotonic() - t) * 1000)
    except OSError:
        return None


def listing(now: float | None = None) -> dict:
    d = load()
    found = d.get("found_at")
    if security_watch._ssh_cmd() and not testguard.active() and (not found or (now or time.time()) - dt.datetime.fromisoformat(found).timestamp() > STALE_S):
        refresh()
    machines = [dict(m, services=[dict(s) for s in m.get("services", [])]) for m in d["machines"]]
    for e in d["manual"]:
        machines.append({"id": e["id"], "name": e["name"], "kind": "added by you", "ip": e["address"], "running": True, "manual": True,
                         "note": e.get("note", ""), "services": [{"port": e["port"], "label": e.get("label") or e["name"], "url": e.get("url", ""),
                                                                  "program": "", "hint": ""}] if e.get("port") else []})
    checks = [(m, s) for m in machines for s in m["services"] if m["ip"]]
    with ThreadPoolExecutor(max_workers=16) as ex:
        ms = list(ex.map(lambda c: _up(c[0]["ip"], c[1]["port"]), checks))
    for (m, s), t in zip(checks, ms):
        s["up"], s["ms"] = t is not None, t
        s["label"] = d["labels"].get(f"{m['ip']}:{s['port']}", s["label"])
    for m in machines:
        m["up"] = any(s.get("up") for s in m["services"]) if m["services"] else (m["running"] and bool(m["ip"]) and _ping_any(m["ip"]))
    return {"machines": machines, "found_at": found, "looking": state["running"], "last_error": state["last_error"],
            "server": bool(security_watch._ssh_cmd())}


def _ping_any(ip: str) -> bool:
    return any(_up(ip, p) is not None for p in (22, 80, 443))


def _address_ok(a: str) -> bool:
    try:
        ipaddress.ip_address(a)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,250})", a))


def add(name: str, address: str, port: int | None = None, note: str = "", url: str = "") -> dict:
    name, address, note, url = name.strip()[:60], address.strip(), note.strip()[:200], url.strip()
    if not name or not _address_ok(address):
        return {"ok": False, "message": "Give it a name and an address, like 192.168.1.1 or router.lan."}
    if port is not None and not 1 <= port <= 65535:
        return {"ok": False, "message": "The port is a number from 1 to 65535."}
    if url and not re.match(r"^(https?|smb)://", url):
        return {"ok": False, "message": "The link starts with http://, https:// or smb://."}
    if port and not url and port not in NOT_WEB:
        web = probe(address, port)
        url = f"{web['scheme']}://{address}:{port}/" if web["scheme"] else ""
    d = load()
    d["manual"].append({"id": uuid.uuid4().hex[:8], "name": name, "address": address, "port": port, "note": note, "url": url})
    save(d)
    return {"ok": True, "message": f"Added {name}."}


def remove(entry_id: str) -> dict:
    d = load()
    keep = [e for e in d["manual"] if e["id"] != entry_id]
    if len(keep) == len(d["manual"]):
        return {"ok": False, "message": "Only machines you added can be removed; found ones come back on the next look."}
    d["manual"] = keep
    save(d)
    return {"ok": True, "message": "Removed."}


def rename(ip: str, port: int, label: str) -> dict:
    d = load()
    key = f"{ip}:{port}"
    if label.strip():
        d["labels"][key] = label.strip()[:60]
    else:
        d["labels"].pop(key, None)
    save(d)
    return {"ok": True, "message": "Renamed." if label.strip() else "Back to the found name."}


def tool(query: str = "") -> dict:
    d = load()
    q = query.strip().lower()
    rows = []
    for m in d["machines"]:
        for s in m.get("services", []):
            label = d["labels"].get(f"{m['ip']}:{s['port']}", s["label"])
            rows.append({"machine": m["name"], "address": m["ip"], "port": s["port"], "service": label, "open": s.get("url") or s.get("hint", "")})
    for e in d["manual"]:
        rows.append({"machine": e["name"], "address": e["address"], "port": e.get("port"), "service": e["name"], "open": e.get("url", ""), "note": e.get("note", "")})
    if q:
        rows = [r for r in rows if q in json.dumps(r).lower()] or rows
    return {"servers": rows[:60], "found_at": d.get("found_at"), "page": "Servers page in Rosie (#/servers)"}
