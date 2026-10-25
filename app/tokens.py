"""Claude token report (owner 2026-10-25: "build a token report into rosie and have you look at it every so often to make sure we are
doing good on tokens"). Reads Claude Code's session logs (<claude home>/projects/**/*.jsonl, subagent logs included) and the local-model
ledger (skills/token-saver/ledger.jsonl) and keeps only numbers and ids, never message text.

* refresh() is incremental: data/token_report.json remembers, per log file, its size, mtime and the byte offset already read, so a
  refresh reads only appended bytes. One refresh reads at most `cap` bytes (newest files first) and carries on next time.
* Each API response counts once: Claude Code writes the same usage on one line per content block, so lines are de-duplicated by
  requestId (or message id).
* Days are local days. Families: opus, sonnet, haiku, other. Thread: main, or sub (a file under subagents/, or isSidechain).
* summary() gives the totals, the per-day series, the top sessions and the plain-language flags; hook_line() is the one line the
  Claude session-start hook shows; `python app/tokens.py` prints the summary for Claude.
Standard library only (the Claude hook imports it) and every read is quiet on errors."""
import datetime as dt
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "token_report.json"
FAMILIES = ("opus", "sonnet", "haiku", "other")
KEEP_DAYS = 40                      # older days are dropped from the cache
MAX_AGE_DAYS = 35                   # log files not touched for this long are skipped
CAP_BYTES = 200 * 1024 * 1024       # most bytes one refresh reads
KEEP_IDS = 30                       # request ids remembered per file (one response's lines sit next to each other)
CLI_CAP = 3 * 1024 * 1024 * 1024    # `python app/tokens.py` reads everything that is new
GOALS = {"token_goal_main_share": 50,          # the main (top) model may write at most this % of the output tokens over 7 days
         "token_goal_cache_read_ratio": 80,    # at least this % of the input should come from the cache over 7 days
         "token_spike_factor": 2.0}            # today's output above this many times the 7-day average is a spike
MIN_SHARE_OUT = 50_000              # a share over fewer output tokens than this says nothing yet
MIN_SPIKE_OUT = 50_000
_lock = threading.Lock()


def claude_home() -> Path:
    env = os.environ.get("ROSIE_CLAUDE_HOME")
    return Path(env) if env else Path.home() / ".claude"


def _goals() -> dict:
    g = dict(GOALS)
    try:
        import config
        cfg = config.load()
        for k in GOALS:
            if isinstance(cfg.get(k), (int, float)) and not isinstance(cfg.get(k), bool):
                g[k] = cfg[k]
    except Exception:
        pass
    return g


def family(model) -> str:
    m = str(model or "").lower()
    for f in FAMILIES[:3]:
        if f in m:
            return f
    return "other"


def _day(ts: str):
    """ISO UTC time -> local day 'YYYY-MM-DD' (None if it can't be read)."""
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone().strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _load() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        if d.get("v") == 1:
            return d
    except (OSError, ValueError):
        pass
    return {"v": 1, "files": {}, "days": {}, "sessions": {}, "ledger": {"off": 0, "days": {}}, "at": 0}


def _save(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE)


def _add(st: dict, day, fam, sub, vals, project, sid):
    """vals = (output, input, cache_read, cache_write)."""
    cell = st["days"].setdefault(day, {}).setdefault(fam, {}).setdefault("sub" if sub else "main", [0, 0, 0, 0, 0])
    for i, v in enumerate(vals):
        cell[i] += v
    cell[4] += 1
    s = st["sessions"].setdefault(sid, {"p": project, "d": {}})
    s["d"].setdefault(day, {})
    s["d"][day][fam] = s["d"][day].get(fam, 0) + vals[0]
    s["d"][day]["n"] = s["d"][day].get("n", 0) + 1
    if sub:
        s["d"][day]["sub"] = s["d"][day].get("sub", 0) + vals[0]


def _read_file(st: dict, path: Path, rec: dict, project: str, budget: int) -> int:
    """Read the new complete lines of one log. Returns the bytes read."""
    sub = "subagents" in path.parts
    sid_dir = path.parent.parent.name if sub else path.stem
    ids = rec.setdefault("ids", [])
    seen = set(ids)
    n_read = 0
    with open(path, "rb") as f:
        f.seek(rec["off"])
        while n_read < budget:
            line = f.readline()
            if not line.endswith(b"\n"):                # half-written last line: read it next time
                break
            rec["off"] += len(line)
            n_read += len(line)
            if b'"usage"' not in line or b'"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            if not isinstance(u, dict) or str(msg.get("model", "")).startswith("<"):
                continue
            rid = d.get("requestId") or msg.get("id")
            if rid:
                if rid in seen:
                    continue
                seen.add(rid)
                ids.append(rid)
            day = _day(d.get("timestamp"))
            if not day:
                continue
            vals = tuple(int(u.get(k) or 0) for k in ("output_tokens", "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            _add(st, day, family(msg.get("model")), sub or bool(d.get("isSidechain")), vals, project, str(d.get("sessionId") or sid_dir))
    del ids[:-KEEP_IDS]
    return n_read


def _read_ledger(st: dict, budget: int = 8 * 1024 * 1024):
    p = claude_home() / "skills" / "token-saver" / "ledger.jsonl"
    led = st.setdefault("ledger", {"off": 0, "days": {}})
    try:
        size = p.stat().st_size
        if size < led["off"]:
            led["off"], led["days"] = 0, {}              # the file was replaced
        with open(p, "rb") as f:
            f.seek(led["off"])
            while budget > 0:
                line = f.readline()
                if not line.endswith(b"\n"):
                    break
                led["off"] += len(line)
                budget -= len(line)
                try:
                    r = json.loads(line)
                    day = str(r["ts"])[:10]
                    off, ret = int(r.get("tokens_offloaded") or 0), int(r.get("tokens_returned") or 0)
                except (ValueError, KeyError, TypeError):
                    continue
                c = led["days"].setdefault(day, [0, 0, 0])      # kept out, offloaded, calls
                c[0] += max(off - ret, 0)
                c[1] += off
                c[2] += 1
    except OSError:
        pass


def _prune(st: dict):
    cut = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k in st["days"] if k < cut]:
        del st["days"][k]
    for k in [k for k in st["ledger"]["days"] if k < cut]:
        del st["ledger"]["days"][k]
    for sid in list(st["sessions"]):
        s = st["sessions"][sid]
        s["d"] = {d: v for d, v in s["d"].items() if d >= cut}
        if not s["d"]:
            del st["sessions"][sid]


def refresh(cap: int = CAP_BYTES, budget_s: float = 0) -> dict:
    """Read what is new in the logs (at most `cap` bytes, newest files first; `budget_s` > 0 also stops after that many seconds),
    save the cache and return it. Several callers at once: the others wait."""
    with _lock:
        st = _load()
        proj = claude_home() / "projects"
        t0 = time.time()
        cutoff = time.time() - MAX_AGE_DAYS * 86400
        found = []
        try:
            for p in proj.rglob("*.jsonl"):
                try:
                    s = p.stat()
                except OSError:
                    continue
                if s.st_mtime >= cutoff:
                    found.append((s.st_mtime, s.st_size, p))
        except OSError:
            pass
        found.sort(reverse=True)
        live = {str(p) for _, _, p in found}
        st["files"] = {k: v for k, v in st["files"].items() if k in live}
        left = cap
        for mtime, size, p in found:
            rec = st["files"].setdefault(str(p), {"size": 0, "mtime": 0, "off": 0, "ids": []})
            if size < rec["off"]:                                   # replaced or truncated: start over for this file
                rec.update(off=0, ids=[])
            if size > rec["off"] and left > 0 and not (budget_s and time.time() - t0 > budget_s):
                try:
                    project = p.relative_to(proj).parts[0]
                    left -= _read_file(st, p, rec, project, left)
                except OSError:
                    pass
            rec["size"], rec["mtime"] = size, mtime
        st["pending"] = sum(max(r["size"] - r["off"], 0) for r in st["files"].values())
        _read_ledger(st)
        _prune(st)
        st["at"] = time.time()
        try:
            _save(st)
        except OSError:
            pass
        return st


# ---------------------------------------------------------------- numbers

def _dayrow(st: dict, day: str) -> dict:
    row = {"day": day, "out": 0, "in": 0, "cache_read": 0, "cache_write": 0, "n": 0, "main": 0, "sub": 0,
           "fam": {f: 0 for f in FAMILIES}}
    for fam, threads in st["days"].get(day, {}).items():
        for th, v in threads.items():
            row["out"] += v[0]
            row["in"] += v[1]
            row["cache_read"] += v[2]
            row["cache_write"] += v[3]
            row["n"] += v[4]
            row[th] += v[0]
            row["fam"][fam if fam in row["fam"] else "other"] += v[0]
    lk = st["ledger"]["days"].get(day, [0, 0, 0])
    row["kept_out"], row["ledger_calls"] = lk[0], lk[2]
    return row


def _total(rows: list) -> dict:
    t = {"out": 0, "in": 0, "cache_read": 0, "cache_write": 0, "n": 0, "main": 0, "sub": 0, "kept_out": 0, "ledger_calls": 0,
         "fam": {f: 0 for f in FAMILIES}}
    for r in rows:
        for k in ("out", "in", "cache_read", "cache_write", "n", "main", "sub", "kept_out", "ledger_calls"):
            t[k] += r[k]
        for f in FAMILIES:
            t["fam"][f] += r["fam"][f]
    t["main_share"] = round(100 * t["fam"]["opus"] / t["out"]) if t["out"] else 0
    inp = t["in"] + t["cache_read"] + t["cache_write"]
    t["cache_ratio"] = round(100 * t["cache_read"] / inp) if inp else 0
    return t


def _fmt(n) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def flags(today: dict, week: dict, prior: list, goals: dict = None) -> list:
    """Plain-language flags: [{"id", "text"}]; the first one is the most urgent."""
    g = goals or _goals()
    out = []
    avg = sum(r["out"] for r in prior) / len(prior) if prior else 0
    f = float(g["token_spike_factor"])
    if avg and today["out"] >= MIN_SPIKE_OUT and today["out"] > f * avg:
        out.append({"id": "spike", "text": f"Today's output is {today['out'] / avg:.1f}x your daily average ({_fmt(today['out'])} vs {_fmt(avg)}): "
                                           "check which session is writing so much and move its bulk work to a subagent or the local model."})
    if week["out"] >= MIN_SHARE_OUT and week["main_share"] > g["token_goal_main_share"]:
        out.append({"id": "main_share", "text": f"The main model wrote {week['main_share']}% of output this week (goal {g['token_goal_main_share']}% or less): "
                                                "hand more bulk work to Sonnet and Haiku subagents or `lm`."})
    if week["in"] + week["cache_read"] + week["cache_write"] >= 1_000_000 and week["cache_ratio"] < g["token_goal_cache_read_ratio"]:
        out.append({"id": "cache", "text": f"Only {week['cache_ratio']}% of input came from the cache this week (goal {g['token_goal_cache_read_ratio']}% or more): "
                                           "long idle gaps and many fresh sessions rebuild the context; keep working in one session and avoid needless /clear."})
    return out


def _report(st: dict) -> dict:
    """Everything the page, the hook and the CLI show, computed from the cache."""
    today_d = dt.date.today()
    days = [(today_d - dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    rows = [_dayrow(st, d) for d in days]
    today, week, month = rows[-1], _total(rows[-7:]), _total(rows)
    td = _total([today])
    prior = rows[-8:-1]
    cut = days[-7]
    top = []
    for sid, s in st["sessions"].items():
        out, mix, sub, n = 0, {}, 0, 0
        for d, v in s["d"].items():
            if d >= cut:
                for fam in FAMILIES:
                    if v.get(fam):
                        mix[fam] = mix.get(fam, 0) + v[fam]
                        out += v[fam]
                sub += v.get("sub", 0)
                n += v.get("n", 0)
        if out:
            top.append({"project": s["p"], "session": sid[:8], "out": out, "responses": n, "sub_out": sub,
                        "models": {f: round(100 * x / out) for f, x in sorted(mix.items(), key=lambda kv: -kv[1])}})
    top.sort(key=lambda r: -r["out"])
    g = _goals()
    fl = flags(td, week, prior, g)
    led_days = st["ledger"]["days"]
    return {"today": td, "week": week, "month": month, "series": rows[-14:], "series30": [{"day": r["day"], "out": r["out"]} for r in rows],
            "by_model": week["fam"], "threads": {"main": week["main"], "sub": week["sub"]}, "top_sessions": top[:12],
            "ledger": {"today": rows[-1]["kept_out"], "week": week["kept_out"], "month": month["kept_out"], "calls_week": week["ledger_calls"],
                       "all": sum(v[0] for v in led_days.values())},
            "flags": fl, "goals": {k: g[k] for k in GOALS}, "pending_bytes": st.get("pending", 0), "updated": st.get("at", 0)}


def summary(refresh_now: bool = False) -> dict:
    """The report from the cache; refresh_now reads the new log bytes first."""
    st = refresh() if refresh_now else _load()
    rep = _report(st)                  # recomputed: a cache from yesterday must still show today as today
    rep["updated"] = st.get("at", 0)
    return rep


def text(rep: dict) -> str:
    t, w, m, k = rep["today"], rep["week"], rep["month"], rep["ledger"]
    lines = [f"Claude tokens (output / input / cache read), updated {time.strftime('%Y-%m-%d %H:%M', time.localtime(rep['updated']))}",
             f"Today:   {_fmt(t['out'])} out, main model {t['main_share']}% of output, {t['n']} responses",
             f"7 days:  {_fmt(w['out'])} out, {_fmt(w['in'])} in, {_fmt(w['cache_read'])} cache read ({w['cache_ratio']}% of input); main model {w['main_share']}% (goal {rep['goals']['token_goal_main_share']}% or less)",
             f"30 days: {_fmt(m['out'])} out, main model {m['main_share']}%",
             "7-day output by model: " + ", ".join(f"{f} {_fmt(v)}" for f, v in rep["by_model"].items() if v) + f"; main thread {_fmt(rep['threads']['main'])}, subagents {_fmt(rep['threads']['sub'])}",
             f"Kept out of Claude by the local model: today {_fmt(k['today'])}, 7 days {_fmt(k['week'])}, all time {_fmt(k['all'])} ({k['calls_week']} calls this week)",
             "Last 7 days (output): " + ", ".join(f"{r['day'][5:]} {_fmt(r['out'])}" for r in rep["series"][-7:])]
    if rep["top_sessions"]:
        lines.append("Top sessions this week:")
        lines += [f"  {s['project'][:34]} {s['session']}  {_fmt(s['out'])} out  " + "/".join(f"{f} {p}%" for f, p in s["models"].items()) for s in rep["top_sessions"][:5]]
    lines.append("Flags: " + ("none, on track." if not rep["flags"] else ""))
    lines += ["  - " + f["text"] for f in rep["flags"]]
    if rep["pending_bytes"]:
        lines.append(f"(still reading older logs: {rep['pending_bytes'] // (1024 * 1024)} MB left)")
    return "\n".join(lines)


def hook_line():
    """The one line the Claude session-start hook shows, from the cache only (never a scan); None if there is no cache."""
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if st.get("v") != 1 or not st.get("at"):
            return None
        rep = _report(st)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    t, w = rep["today"], rep["week"]
    line = f"Tokens: today {_fmt(t['out'])} out (main {t['main_share']}%), 7 days {_fmt(w['out'])}; goal main ≤ {rep['goals']['token_goal_main_share']}%"
    line += " — " + (rep["flags"][0]["text"] if rep["flags"] else "on track")
    if time.time() - st["at"] > 3600:
        line += f" (as of {time.strftime('%H:%M', time.localtime(st['at']))})"
    return line


def job(now, cfg, add=None, background=True):
    """Hourly: refresh the report (capped, at most 20 s) and, once a day when a flag is active, put one item in the Inbox."""
    if cfg.get("token_report_on", True) is False:
        return None
    last = _JOB.get("at")
    if last and (now - last).total_seconds() < 3600:
        return None
    _JOB["at"] = now

    def run():
        try:
            rep = _report(refresh(cap=100 * 1024 * 1024, budget_s=20))
            if rep["flags"] and add:
                day = now.strftime("%Y-%m-%d")
                add("system", "Claude token use: " + rep["flags"][0]["text"].split(":")[0][:100], "\n".join(f["text"] for f in rep["flags"])[:580], "#/tokens",
                    key=f"tokens:{day}", now=now, desktop=False)
        except Exception:
            pass
    if not background:
        run()
        return None
    t = threading.Thread(target=run, daemon=True, name="tokens")
    t.start()
    return t


_JOB = {}


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--cached":
        print(text(summary()))
    else:
        refresh(cap=CLI_CAP)
        print(text(summary()))
