import datetime as dt
import ipaddress
import json
import re
import secrets
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import atomic

INTERNET = "1.1.1.1"
COUNT = 10
KEEP_S = 7 * 86400
EVERY_S = 15 * 60
BAD_LOSS = 5.0
BAD_MS = 150.0
BAD_JITTER = 30.0
IS_WIN = sys.platform == "win32"
FILE = Path(__file__).resolve().parent.parent / "data" / "netquality.jsonl"
RTT_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.I)
_busy = threading.Lock()
_file_lock = threading.Lock()


def _run(argv, timeout):
    kw = {"capture_output": True, "text": True, "timeout": timeout, "encoding": "utf-8", "errors": "replace"}
    if IS_WIN:
        kw["creationflags"] = 0x08000000
    return subprocess.run(argv, **kw)


def _ip(text):
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        return None


def gateway():
    try:
        if IS_WIN:
            out = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop"], 5).stdout
            return _ip(out.strip().splitlines()[0]) if out.strip() else None
        m = re.search(r"^default via (\S+)", _run(["ip", "route", "show", "default"], 5).stdout, re.M)
        return _ip(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def _stats(host, sent, rtts):
    got = len(rtts)
    res = {"host": host, "sent": sent, "got": got, "loss": round(100 * (sent - got) / sent, 1), "avg_ms": None, "jitter_ms": None, "max_ms": None}
    if got:
        diffs = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
        res.update(avg_ms=round(sum(rtts) / got, 1), max_ms=round(max(rtts), 1), jitter_ms=round(sum(diffs) / len(diffs), 1) if diffs else 0.0)
    return res


def ping(host, count=COUNT):
    host = _ip(host)
    if not host:
        raise ValueError("not an IP address")
    argv = ["ping", "-n", str(count), "-w", "2000", host] if IS_WIN else ["ping", "-n", "-c", str(count), "-i", "0.2", "-W", "2", host]
    try:
        out = _run(argv, count * 2.5 + 5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    rtts = [float(x) for x in RTT_RE.findall(out)][:count]
    return _stats(host, count, rtts)


def dns_ms():
    res = {}

    def look():
        t = time.perf_counter()
        try:
            socket.getaddrinfo(f"rosie-{secrets.token_hex(4)}.example.com", 443)
        except (socket.gaierror, OSError):
            pass
        res["ms"] = round((time.perf_counter() - t) * 1000, 1)
    th = threading.Thread(target=look, daemon=True)
    th.start()
    th.join(5)
    return res.get("ms")


def sample(now=None):
    with ThreadPoolExecutor(3) as ex:
        gw = gateway()
        f_gw = ex.submit(ping, gw) if gw else None
        f_net = ex.submit(ping, INTERNET)
        f_dns = ex.submit(dns_ms)
        return {"at": time.time() if now is None else now, "gw": f_gw.result() if f_gw else None, "net": f_net.result(), "dns_ms": f_dns.result()}


def load(since=0):
    out = []
    try:
        lines = FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            s = json.loads(line)
        except ValueError:
            continue
        if isinstance(s, dict) and isinstance(s.get("at"), (int, float)) and s["at"] >= since:
            out.append(s)
    return out


def save(s, now=None):
    now = time.time() if now is None else now
    with _file_lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        with FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(s) + "\n")
        try:
            n = sum(1 for _ in FILE.open(encoding="utf-8"))
        except OSError:
            return
        if n > 2 * KEEP_S / EVERY_S:
            keep = load(now - KEEP_S)
            atomic.write_text(FILE, "".join(json.dumps(x) + "\n" for x in keep))


def bad(s):
    out = []
    net, gw, d = s.get("net") or {}, s.get("gw") or {}, s.get("dns_ms")
    if (net.get("loss") or 0) >= BAD_LOSS:
        out.append(f"{net['loss']:g}% packet loss")
    if (net.get("avg_ms") or 0) >= BAD_MS:
        out.append(f"slow replies ({net['avg_ms']:g} ms)")
    if (net.get("jitter_ms") or 0) >= BAD_JITTER:
        out.append(f"unsteady (jitter {net['jitter_ms']:g} ms)")
    if (gw.get("loss") or 0) >= BAD_LOSS:
        out.append(f"drops on your home network ({gw['loss']:g}%)")
    if d is None or d >= 1000:
        out.append("slow DNS")
    return out


def _now_line(s):
    net, gw = s.get("net") or {}, s.get("gw")
    if not net.get("got"):
        return "Now: no replies from the internet (100% loss)."
    loss = net["loss"]
    parts = [f"{net['avg_ms']:g} ms to the internet", f"jitter {net['jitter_ms']:g} ms", "no loss" if not loss else f"{loss:g}% loss"]
    line = "Now: " + ", ".join(parts)
    if gw:
        line += f"; home network {gw['avg_ms']:g} ms" if gw.get("got") else "; no replies from your router"
        if gw.get("got") and gw["loss"]:
            line += f" ({gw['loss']:g}% loss)"
    d = s.get("dns_ms")
    return line + (f"; DNS {d:g} ms." if d is not None else "; DNS did not answer.")


def report(hours=24, now=None, fresh=True):
    now = time.time() if now is None else now
    cur = None
    if fresh:
        cur = sample(now)
        save(cur, now)
    rows = load(now - hours * 3600)
    flagged = [(s, bad(s)) for s in rows]
    flagged = [(s, r) for s, r in flagged if r]
    worst = None
    if flagged:
        s, r = max(flagged, key=lambda x: (len(x[1]), (x[0].get("net") or {}).get("loss") or 0))
        worst = {"at": dt.datetime.fromtimestamp(s["at"]).strftime("%Y-%m-%d %H:%M"), "reasons": r}
    nets = [s["net"] for s in rows if isinstance(s.get("net"), dict)]
    ms = [n["avg_ms"] for n in nets if n.get("avg_ms") is not None]
    jit = [n["jitter_ms"] for n in nets if n.get("jitter_ms") is not None]
    lines = [_now_line(cur)] if cur else []
    if not rows:
        lines.append("No history yet.")
    elif flagged:
        lines.append(f"Last {hours} h: {len(flagged)} of {len(rows)} checks had problems.")
        lines.append(f"Worst at {worst['at']}: {', '.join(worst['reasons'])}.")
    else:
        lines.append(f"Last {hours} h: the one check was fine." if len(rows) == 1 else f"Last {hours} h: all {len(rows)} checks were fine.")
    return {"now": cur, "window_h": hours, "samples": len(rows), "bad": len(flagged), "worst": worst,
            "median_ms": round(statistics.median(ms), 1) if ms else None, "median_jitter_ms": round(statistics.median(jit), 1) if jit else None,
            "avg_loss": round(sum(n.get("loss") or 0 for n in nets) / len(nets), 1) if nets else None, "summary": lines}


def job(now_dt, cfg, mark=None):
    if not cfg.get("net_quality_on", True):
        return
    rows = load(time.time() - EVERY_S)
    if rows and time.time() - max(s["at"] for s in rows) < EVERY_S - 30:
        return
    if not _busy.acquire(blocking=False):
        return

    def run():
        try:
            save(sample())
            if mark:
                mark(True)
        except Exception as e:
            if mark:
                mark(False, type(e).__name__)
        finally:
            _busy.release()
    threading.Thread(target=run, daemon=True, name="net-quality").start()


def tool(hours=24):
    try:
        h = max(1, min(168, int(hours)))
    except (TypeError, ValueError):
        h = 24
    try:
        return report(hours=h)
    except Exception as e:
        return {"error": f"Couldn't check the internet: {type(e).__name__}"}
