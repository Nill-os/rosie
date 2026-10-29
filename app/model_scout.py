# board #748 owner-research-abliterated-models
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

import config
import corner
import models
import testguard
import web
import util

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "model_scout.json"
OLLAMA = "http://127.0.0.1:11434"
HF = "https://huggingface.co"
SEARCH = (HF + "/api/models?search=abliterated&filter=gguf&sort=createdAt&direction=-1&limit=100"
          "&expand[]=gguf&expand[]=createdAt&expand[]=downloads&expand[]=likes&expand[]=cardData")
RESEARCH_EVERY_S = 12 * 3600
MIN_PARAMS, MAX_PARAMS = 6e9, 12e9  # board #748 why-model-size-band
QUANT = "Q4_K_M"
NOT_CHAT = re.compile(r"lora|control.?vector|encoder|upsampler|embed|reward|draft|tts|whisper|(?:^|[-_/])base(?:$|[-_])", re.I)
MIN_AGE_H = 24  # board #748 why-wait-day-before-test
TEST_FROM, TEST_TO = dt.time(0, 30), dt.time(1, 45)
AWAY_S = 1800
MIN_FREE_GB = 25
PULL_TIMEOUT_S = 3600
EVAL_TIMEOUT_S = 1800
FASTER = 0.75
HOLD = "Paused while Rosie tests a new AI model (model scout)."
SCORE = re.compile(r"-> (\d+)/(\d+) correct, (\d+)/\d+ without needless questions, (\d+)s total")
_lock = threading.Lock()
state = {"running": False, "doing": "", "last_error": ""}


def load() -> dict:
    try:
        d = json.loads(STATE.read_text())
    except (OSError, ValueError):
        d = {}
    d.setdefault("seen", {})
    return d


def save(d: dict):
    testguard.check(STATE)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(STATE)


_now = util.now_dt


def _iso(t: dt.datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


def _get_json(url: str):
    r = web.get_public(url, max_bytes=4_000_000, accept=("application/json",), timeout=30)
    if r.get("error") or r.get("status") != 200:
        raise RuntimeError(f"Hugging Face didn't answer ({r.get('error') or r.get('status')})")
    return json.loads(r["body"])


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z]+\d+(?:\.\d+)?|\d+(?:\.\d+)?b\b", name.lower().replace("_", "-").replace(":", "-")))


def _base(m: dict) -> str:
    b = (m.get("cardData") or {}).get("base_model")
    b = b[0] if isinstance(b, list) and b else b
    return str(b or re.sub(r"(?i)[-_.](i1-)?gguf$", "", m["id"])).lower()


def _same_as_current(base: str, current: str) -> bool:
    want = _tokens(current)
    return bool(want) and want <= _tokens(base)


def judge(m: dict, files: list[dict] | None, current: str, bases: set[str]) -> dict:
    rid, g = m["id"], m.get("gguf") or {}
    params, base = g.get("total"), _base(m)
    rec = {"found": _iso(), "created": m.get("createdAt", ""), "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
           "params_b": round(params / 1e9, 1) if params else None, "base": base, "arch": g.get("architecture", "")}
    why = ("not a chat model" if NOT_CHAT.search(rid) or NOT_CHAT.search(base.split("/")[-1]) else
           "size unknown" if not params else
           f"{params / 1e9:.0f}B parameters (needs {MIN_PARAMS / 1e9:.0f} to {MAX_PARAMS / 1e9:.0f})" if not MIN_PARAMS <= params <= MAX_PARAMS else
           "the same model she is on" if _same_as_current(base, current) else
           "a copy of one already found" if base in bases else "")
    if why or files is None:
        return {**rec, "status": "skipped" if why else "", "why": why}
    q = [f for f in files if f.get("type") == "file" and re.search(QUANT + r"\.gguf$", f.get("path", ""), re.I)]
    if not q:
        return {**rec, "status": "skipped", "why": f"no {QUANT} file"}
    size = int(q[0].get("size") or 0)
    if size > models.GPU_FIT_BYTES:
        return {**rec, "status": "skipped", "why": f"{size / 1e9:.1f} GB: too big for the graphics card"}
    return {**rec, "status": "candidate", "why": "", "file_gb": round(size / 1e9, 1), "pull": f"hf.co/{rid}:{QUANT}"}


def research() -> int:
    d = load()
    current = models.current()
    bases = {r["base"] for r in d["seen"].values() if r.get("status") in ("candidate", "tested", "flagged", "failed")}
    found = 0
    for m in _get_json(SEARCH):
        rid = m.get("id")
        if not rid or rid in d["seen"]:
            continue
        rec = judge(m, None, current, bases)
        if not rec["status"]:
            try:
                rec = judge(m, _get_json(f"{HF}/api/models/{rid}/tree/main"), current, bases)
            except (RuntimeError, ValueError) as e:
                rec = {**rec, "status": "skipped", "why": f"file list unreadable ({str(e)[:80]})"}
        d["seen"][rid] = rec
        if rec["status"] == "candidate":
            bases.add(rec["base"])
            found += 1
    d["researched_at"] = _iso()
    save(d)
    return found


def research_due(d: dict, now: dt.datetime) -> bool:
    try:
        return (now - dt.datetime.fromisoformat(d["researched_at"])).total_seconds() >= RESEARCH_EVERY_S
    except (KeyError, TypeError, ValueError):
        return True


def next_candidate(d: dict, now: dt.datetime) -> tuple[str, dict] | None:
    ok = []
    for rid, r in d["seen"].items():
        if r.get("status") != "candidate":
            continue
        try:
            age_h = (now - dt.datetime.fromisoformat(r["created"].replace("Z", "+00:00"))).total_seconds() / 3600
        except (KeyError, ValueError):
            age_h = MIN_AGE_H
        if age_h >= MIN_AGE_H:
            ok.append((r.get("downloads", 0) + 50 * r.get("likes", 0), rid, r))
    return max(ok)[1:] if ok else None


def test_due(d: dict, now: dt.datetime) -> str | None:
    if not TEST_FROM <= now.time() <= TEST_TO:
        return "outside the test window"
    if d.get("tested_on") == now.date().isoformat():
        return "already tested one tonight"
    if corner.owner_recent(AWAY_S):
        return "the owner was here recently"
    if corner.GPU_HOLD:
        return corner.GPU_HOLD
    if shutil.disk_usage(Path.home()).free < MIN_FREE_GB * 1e9:
        return "not enough free disk space"
    return corner._load_probe(now, corner.settings())


def _staging() -> bool:
    try:
        return time.time() - corner.STAGE_FLAG.stat().st_mtime < 3600
    except OSError:
        return False


def _pull(name: str):
    with httpx.Client(timeout=httpx.Timeout(PULL_TIMEOUT_S, connect=5)) as c, c.stream("POST", OLLAMA + "/api/pull", json={"model": name, "stream": True}) as r:
        r.raise_for_status()
        last = ""
        for line in r.iter_lines():
            if line.strip():
                j = json.loads(line)
                if j.get("error"):
                    raise RuntimeError(str(j["error"])[:200])
                last = j.get("status", "")
        if last != "success":
            raise RuntimeError("the download didn't finish")


def _delete(name: str):
    try:
        httpx.request("DELETE", OLLAMA + "/api/delete", json={"model": name}, timeout=60)
    except httpx.HTTPError:
        pass


def _installed(name: str) -> bool:
    try:
        return any(m.get("name") == name for m in httpx.get(OLLAMA + "/api/tags", timeout=30).json().get("models") or [])
    except (httpx.HTTPError, ValueError):
        return True  # board #748 safety-unsure-means-owners


def _caps(name: str) -> list:
    return httpx.post(OLLAMA + "/api/show", json={"model": name}, timeout=60).json().get("capabilities") or []


def run_eval(current: str, cand: str) -> dict:
    p = subprocess.Popen([sys.executable, str(ROOT / "tests" / "model_eval.py"), current, cand], cwd=str(ROOT), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)
    t0, out = time.time(), []
    reader = threading.Thread(target=lambda: out.extend(p.stdout), daemon=True)
    reader.start()
    try:
        while p.poll() is None:
            if corner.owner_recent(60):
                raise Interrupted("you came back")
            if _staging():
                raise Interrupted("a new version of Rosie is being tested")
            if time.time() - t0 > EVAL_TIMEOUT_S:
                raise RuntimeError("the benchmark took too long")
            time.sleep(2)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    reader.join(5)
    scores = [SCORE.search(line) for line in out]
    scores = [m for m in scores if m]
    if len(scores) != 2:
        raise RuntimeError("the benchmark gave no score")
    return {name: {"right": int(m[1]), "of": int(m[2]), "clean": int(m[3]), "secs": int(m[4])} for name, m in zip((current, cand), scores)}


class Interrupted(Exception):
    pass


def better(a: dict, b: dict) -> bool:
    if a["right"] != b["right"]:
        return a["right"] > b["right"]
    if a["clean"] != b["clean"]:
        return a["clean"] > b["clean"]
    return a["secs"] <= FASTER * b["secs"]


def _add(x: dict, y: dict) -> dict:
    return {k: x[k] + y[k] for k in x}


def test(rid: str, rec: dict, now: dt.datetime | None = None):
    import corner_browse
    import inbox
    now = now or _now()
    d = load()
    d["tested_on"] = now.date().isoformat()
    save(d)
    name, current, keep = rec["pull"], models.current(), False
    result = {"tested": _iso()}
    try:
        keep = _installed(name)
        state["doing"] = f"Downloading {rid}"
        _pull(name)
        caps = _caps(name)
        if "completion" not in caps or "tools" not in caps:
            result.update(status="failed", why="it can't use tools (Rosie needs them)")
            return
        state["doing"] = f"Testing {rid}"
        corner.GPU_HOLD = HOLD
        first = run_eval(current, name)
        runs = [first]
        if better(first[name], first[current]):
            runs.append(run_eval(current, name))
        mine = [r[name] for r in runs]
        theirs = [r[current] for r in runs]
        won = len(runs) == 2 and all(better(a, b) for a, b in zip(mine, theirs))
        a, b = mine[0] if len(runs) == 1 else _add(*mine), theirs[0] if len(runs) == 1 else _add(*theirs)
        result.update(status="flagged" if won else "tested", why="" if won else "not better than her current model",
                      score=f"{a['right']}/{a['of']}", current_score=f"{b['right']}/{b['of']}",
                      secs_per_task=round(a["secs"] / a["of"], 1), current_secs_per_task=round(b["secs"] / b["of"], 1), runs=len(runs))
        if won:
            keep = True
            inbox.add("model", f"A better AI model for Rosie: {rid}",
                      f"On her own everyday tasks ({len(runs)} runs) it got {a['right']} of {a['of']} right, {a['clean']} without needless questions, "
                      f"{a['secs'] / a['of']:.1f} s per task. Her current model {current}: {b['right']} of {b['of']}, {b['clean']}, "
                      f"{b['secs'] / b['of']:.1f} s. It is downloaded as {name}; to try it, pick it in Settings > AI model.",
                      link="#/settings", key=f"model-scout:{rid}")
    except Interrupted as e:
        result.update(status="candidate", why=f"test stopped: {e}; tried again another night")
        d = load()
        d["tested_on"] = ""
        save(d)
    except Exception as e:
        result.update(status="failed", why=f"{type(e).__name__}: {str(e)[:160]}")
        state["last_error"] = f"{rid}: {result['why']}"
    finally:
        if corner.GPU_HOLD == HOLD:
            corner.GPU_HOLD = ""
        if not keep:
            _delete(name)
        corner_browse.warm_chat()
        d = load()
        d["seen"][rid] = {**d["seen"].get(rid, rec), **result}
        save(d)
        state.update(running=False, doing="")


def job(now: dt.datetime, cfg: dict):
    if not cfg.get("model_scout", True) or state["running"] or testguard.active():
        return
    d = load()
    if research_due(d, now):
        target, args = _research_thread, ()
    else:
        pick = None if test_due(d, now) else next_candidate(d, now)
        if not pick:
            return
        target, args = test, pick
    with _lock:
        if state["running"]:
            return
        state["running"] = True
    def guarded():
        try:
            target(*args)
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        finally:
            state.update(running=False, doing="")
    threading.Thread(target=guarded, daemon=True, name="model-scout").start()


def _research_thread():
    try:
        state["doing"] = "Looking for new models"
        research()
        state["last_error"] = ""
    except Exception as e:
        state["last_error"] = f"research: {type(e).__name__}: {str(e)[:160]}"
        d = load()
        d["researched_at"] = _iso()
        save(d)
    finally:
        state.update(running=False, doing="")


def status() -> dict:
    d = load()
    seen = d["seen"].values()
    done = sorted((dict(r, id=k) for k, r in d["seen"].items() if r.get("tested")), key=lambda r: r["tested"], reverse=True)
    return {"on": bool(config.load().get("model_scout", True)), "doing": state["doing"], "last_error": state["last_error"],
            "researched_at": d.get("researched_at"), "looked_at": len(d["seen"]),
            "candidates": sum(r.get("status") == "candidate" for r in seen),
            "flagged": [r["id"] for r in done if r.get("status") == "flagged"],
            "last_test": {k: done[0].get(k) for k in ("id", "status", "why", "score", "current_score", "tested")} if done else None}
