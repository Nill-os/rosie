#!/usr/bin/env python3
# board #815 safety-slots-outside-assistant
import json
import os
import secrets as _secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
OPS = Path(os.environ.get("ROSIE_OPS_DIR") or HOME / "rosie-ops")
SLOT = {"A": Path(os.environ.get("ROSIE_SLOT_A") or HOME / "assistant"), "B": Path(os.environ.get("ROSIE_SLOT_B") or HOME / "assistant-b")}
PORT = int(os.environ.get("ROSIE_PORT") or 8765)
STAGE_PORT = int(os.environ.get("ROSIE_STAGE_PORT") or 8780)
PROBATION_MIN = 30
SHARED = ["data", "secrets", "vault", ".venv", "vendor/gi", "android/app/build", "android/app/src/main/assets/model-en-us", "android/local.properties"]
PROTECTED = ("ops", "start.sh", "sync-repo.sh", "backup-data.sh", "rotate-code.sh", "login.sh", "login-gmail.sh", ".git", ".github")
STATE = OPS / "state.json"


def run(cmd, cwd=None, timeout=300, check=False, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))[:80]}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def git(slot, *args, **kw):
    return run(["git", "-C", str(SLOT[slot]), *args], **kw)


def chat_model() -> str:
    try:
        return json.loads((SLOT["A"] / "data" / "config.json").read_text()).get("chat_model") or "qwen3.5:9b"
    except (OSError, ValueError):
        return "qwen3.5:9b"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    s = load_state()
    s.update(kw)
    OPS.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE)
    return s


def active() -> str:
    try:
        v = (OPS / "active").read_text().strip().upper()
        return v if v in SLOT else "A"
    except OSError:
        return "A"


def other(slot: str) -> str:
    return "B" if slot == "A" else "A"


def set_active(slot: str):
    OPS.mkdir(parents=True, exist_ok=True)
    tmp = OPS / "active.tmp"
    tmp.write_text(slot + "\n")
    tmp.replace(OPS / "active")


def log_event(kind: str, **kw):
    OPS.mkdir(parents=True, exist_ok=True)
    with (OPS / "events.jsonl").open("a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **kw}) + "\n")


def events(n=10) -> list:
    try:
        return [json.loads(l) for l in (OPS / "events.jsonl").read_text().splitlines()[-n:]]
    except OSError:
        return []


def head(slot: str) -> str:
    return git(slot, "rev-parse", "--short", "HEAD").stdout.strip() if SLOT[slot].exists() else ""


def dirty(slot: str) -> list:
    return [l for l in git(slot, "status", "--porcelain").stdout.splitlines() if l.strip()] if SLOT[slot].exists() else []


def healthz(port=None, timeout=3):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port or PORT}/healthz", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ensure_b():
    a, b = SLOT["A"], SLOT["B"]
    if not (b / ".git").exists():
        run(["git", "clone", "-q", str(a), str(b)], check=True)
        url = git("A", "remote", "get-url", "origin").stdout.strip()
        if url:
            git("B", "remote", "set-url", "origin", url)
        for name in git("A", "remote").stdout.split():
            if name != "origin" and not git("B", "remote", "get-url", name).stdout.strip():
                git("B", "remote", "add", name, git("A", "remote", "get-url", name).stdout.strip())
    exclude = b / ".git" / "info" / "exclude"
    keep = exclude.read_text() if exclude.exists() else ""
    exclude.write_text(keep + "".join(f"/{p}\n" for p in SHARED if f"/{p}\n" not in keep))
    for rel in SHARED:
        src, dst = a / rel, b / rel
        if src.exists() and not (dst.exists() or dst.is_symlink()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.symlink_to(src)
    return {"slot": "B", "path": str(b), "head": head("B")}


def _board():
    try:
        app = str(SLOT[active()] / "app")
        if "agentboard" not in sys.modules and app not in sys.path:
            sys.path.append(app)
        import agentboard
        return agentboard
    except Exception:
        return None


def _board_call(fn, *a, **k):
    b = _board()
    try:
        return getattr(b, fn)(*a, **k) if b else None
    except Exception:
        return None


def claimed(slot: str) -> str:
    return _board_call("blocked_by", f"slot-{slot}") or ""


def claim(why="working on it", force=False):
    idle = other(active())
    return _board_call("claim", f"slot-{idle}", why, 12, force=force) or {"error": "The agent board is not available."}


def release():
    return _board_call("release", f"slot-{other(active())}") or {"ok": True}


def prepare(topic="update", force=False):
    live = active()
    idle = other(live)
    if claimed(idle) and not force:
        return {"error": claimed(idle), "claimed": True}
    if (load_state().get("probation_until") or 0) > time.time() and not force:  # board #815 why-idle-slot-way-back
        return {"error": f"Slot {idle} is the way back while the new version is on probation (about {int((load_state()['probation_until'] - time.time()) // 60) + 1} more minutes). Wait, or prepare with force."}
    if idle == "B":
        ensure_b()
    if not SLOT[idle].exists():
        raise RuntimeError(f"slot {idle} does not exist")
    if dirty(idle) and not force:
        return {"error": f"Slot {idle} has uncommitted work ({len(dirty(idle))} files). Commit it, or prepare with force to throw it away.", "dirty": dirty(idle)[:10]}
    got = _board_call("claim", f"slot-{idle}", topic, 12, force=force)  # board #815 fix-claim-before-change
    if isinstance(got, dict) and got.get("ok") is False:
        return {"error": claimed(idle) or f"slot {idle} is claimed by another agent", "claimed": True}
    if dirty(live):
        git(live, "add", "-A")
        git(live, "-c", "user.name=Rosie", "-c", "user.email=rosie@localhost", "commit", "-q", "-m", "Snapshot before self-update")
    base = head(live)
    run(["git", "-C", str(SLOT[idle]), "fetch", "-q", str(SLOT[live]), "HEAD"], check=True)
    branch = "rosie/" + "".join(c if c.isalnum() or c in "-_" else "-" for c in topic)[:40] + time.strftime("-%m%d-%H%M")
    git(idle, "checkout", "-q", "-f", "-B", branch, "FETCH_HEAD", check=True)
    git(idle, "clean", "-fdq")
    save_state(prepared={"slot": idle, "base": base, "branch": branch, "at": time.time()}, last_stage=None)
    log_event("prepare", slot=idle, base=base, branch=branch)
    _board_call("post", f"prepared Rosie slot {idle} on {branch} (from live {base}) for: {topic}", "doing")
    return {"slot": idle, "path": str(SLOT[idle]), "base": base, "branch": branch}


def commit(message="Self-update"):
    idle = other(active())
    if claimed(idle):
        return {"error": claimed(idle), "claimed": True}
    if not dirty(idle):
        return {"ok": True, "note": "nothing to commit", "head": head(idle)}
    git(idle, "add", "-A")
    git(idle, "-c", "user.name=Rosie", "-c", "user.email=rosie@localhost", "commit", "-q", "-m", message, check=True)
    return {"ok": True, "head": head(idle)}


def diff():
    idle = other(active())
    base = (load_state().get("prepared") or {}).get("base") or "HEAD"
    stat = git(idle, "diff", "--stat", base).stdout.strip().splitlines()
    files = git(idle, "diff", "--name-only", base).stdout.split()
    return {"slot": idle, "files": files, "summary": stat[-1].strip() if stat else "no changes", "dirty": len(dirty(idle))}


def _build_stage(idle: str) -> Path:
    src = SLOT[idle]
    live_data = SLOT["A"] / "data"
    tmp = Path(tempfile.mkdtemp(prefix="rosie-stage-"))
    for d in ("app", "static"):
        shutil.copytree(src / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "vendor").mkdir()
    (tmp / "data").mkdir()
    for p in live_data.glob("*.db"):
        shutil.copy2(p, tmp / "data" / p.name)
    for n in ("config.json",):
        if (live_data / n).exists():
            shutil.copy2(live_data / n, tmp / "data" / n)
    if (SLOT["A"] / "vault").exists():
        shutil.copytree(SLOT["A"] / "vault", tmp / "vault")
    (tmp / "secrets").mkdir()
    (tmp / "secrets" / "session_key").write_text(_secrets.token_hex(16))
    (tmp / "secrets" / "access_code").write_text("STAG-ETES-TONL\n")
    return tmp


def smoke(idle: str) -> dict:
    import hashlib
    import hmac
    if healthz(STAGE_PORT, 1):  # board #815 why-own-stage-server-only
        return {"ok": False, "checks": {}, "error": f"port {STAGE_PORT} is already in use (an earlier check's server?)"}
    tmp = _build_stage(idle)
    me = f"{idle}-stage-{_secrets.token_hex(4)}"
    env = {**os.environ, "ROSIE_SANDBOX": "1", "ROSIE_SLOT": me}
    venv = SLOT["A"] / ".venv" / "bin" / "uvicorn"
    proc = subprocess.Popen([str(venv), "server:app", "--host", "127.0.0.1", "--port", str(STAGE_PORT)], cwd=tmp / "app", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    out = {"ok": False, "checks": {}}
    try:
        up = None
        for _ in range(60):
            up = healthz(STAGE_PORT, 2)
            up = up if up and up.get("slot") == me else None
            if up or proc.poll() is not None:
                break
            time.sleep(1)
        out["checks"]["starts"] = bool(up)
        if not up:
            out["error"] = (proc.stderr.read().decode()[-400:] if proc.poll() is not None else "did not answer in 60 seconds")
            return out
        key = (tmp / "secrets" / "session_key").read_text().strip().encode()
        cookie = hmac.new(key, b"assistant-ok", hashlib.sha256).hexdigest()

        def call(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{STAGE_PORT}{path}", data=json.dumps(body).encode() if body is not None else None, headers={"Cookie": f"assistant_session={cookie}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        out["checks"]["home_page"] = bool(call("/api/home").get("date"))
        out["checks"]["asks_time"] = ":" in call("/api/chat", {"text": "what time is it", "mode": "auto", "voice": False}).get("reply", "")
        out["checks"]["voice_endpoint"] = ":" in call("/api/voice", {"text": "what time is it"}).get("reply", "")
        out["checks"]["static_pages"] = urllib.request.urlopen(f"http://127.0.0.1:{STAGE_PORT}/", timeout=10).status == 200
        out["ok"] = all(out["checks"].values())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def stage(with_eval=True):
    idle = other(active())
    if claimed(idle):
        return {"ok": False, "error": claimed(idle) + " Release the claim first (the stage command runs as its own agent), then run stage again.", "claimed": True}
    commit("Self-update (staged)")
    flag = OPS / "staging"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(str(time.time()))
    try:
        return _stage(idle, with_eval)
    finally:
        flag.unlink(missing_ok=True)


def _stage(idle, with_eval):
    py = str(SLOT["A"] / ".venv" / "bin" / "python")
    res = {"slot": idle, "head": head(idle), "started": time.time()}
    t = run([py, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], cwd=SLOT[idle], timeout=900)
    last = t.stdout.strip().splitlines()[-1] if t.stdout.strip() else t.stderr.strip()[-200:]
    res["tests"] = {"ok": t.returncode == 0, "summary": last}
    res["smoke"] = smoke(idle) if res["tests"]["ok"] else {"ok": False, "skipped": "tests failed"}
    res["eval"] = {"ok": True, "skipped": True}
    if with_eval and res["tests"]["ok"] and res["smoke"]["ok"] and (SLOT[idle] / "tests" / "model_eval.py").exists():
        import re
        base = int(load_state().get("eval_baseline", 12))
        passed, total, runs, failed = 0, 0, [], []
        for _ in range(2):
            e = run([py, "tests/model_eval.py", chat_model()], cwd=SLOT[idle], timeout=1200)
            m = re.search(r"-> (\d+)/(\d+) correct", e.stdout)
            p1, t1 = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            runs.append(p1)
            if p1 > passed or not failed:
                failed = [ln.strip()[:90] for ln in e.stdout.splitlines() if ln.strip().startswith("FAIL")]
            if p1 > passed:
                passed, total = p1, t1
            if total and passed >= min(base, total):
                break
        res["eval"] = {"ok": passed >= min(base, total) and total > 0, "score": f"{passed}/{total}", "needs": base, "failed": failed}
    res["ok"] = res["tests"]["ok"] and res["smoke"]["ok"] and res["eval"]["ok"]
    res["finished"] = time.time()
    save_state(last_stage=res)
    log_event("stage", slot=idle, head=res["head"], ok=res["ok"])
    _board_call("post", f"stage check of slot {idle} ({res['head']}) {'passed' if res['ok'] else 'FAILED'}: tests {res['tests']['summary'][:80]}, "
                f"smoke {'ok' if res['smoke']['ok'] else 'failed'}, eval {res['eval'].get('score', 'skipped')}", "done" if res["ok"] else "blocked")
    return res


def restart_service():
    r = run(["bash", "-c", f"ss -ltnp 'sport = :{PORT}' 2>/dev/null | grep -oP 'pid=\\K[0-9]+' | head -1"])
    pid = r.stdout.strip()
    if pid:
        os.kill(int(pid), signal.SIGTERM)
        return True
    return False


def wait_healthy(slot: str, seconds=90) -> bool:
    t0 = time.time()
    time.sleep(3)
    while time.time() - t0 < seconds:
        h = healthz()
        if h and h.get("slot") == slot:
            return True
        time.sleep(2)
    return False


def switch(force=False):
    live, idle = active(), other(active())
    st = load_state()
    ls = st.get("last_stage") or {}
    if not force and not (ls.get("ok") and ls.get("head") == head(idle) and not dirty(idle)):
        return {"error": "Not allowed: the new version has not passed the full check for its exact current code. Run stage first.", "last_stage": ls.get("ok")}
    if claimed(idle) and not force:
        return {"error": claimed(idle), "claimed": True}
    try:
        run([str(SLOT[live] / ".venv" / "bin" / "python"), str(SLOT[live] / "app" / "backup.py"), "backup"], timeout=300)
    except Exception as e:
        log_event("backup-failed", error=str(e)[:200])
    save_state(previous=live, previous_head=head(live), previous_dirty=len(dirty(live)), switched_at=time.time(), probation_until=time.time() + PROBATION_MIN * 60, wd_fails=0)
    set_active(idle)
    log_event("switch", frm=live, to=idle, head=head(idle))
    restart_service()
    if wait_healthy(idle):
        _board_call("release", f"slot-{idle}", force=True)
        _board_call("post", f"switched Rosie to slot {idle} ({head(idle)}); slot {live} is idle now", "done")
        return {"ok": True, "active": idle, "head": head(idle), "probation_minutes": PROBATION_MIN, "note": f"If anything goes wrong in the next {PROBATION_MIN} minutes I go back automatically."}
    rb = rollback("new version did not come up healthy")
    return {"ok": False, "rolled_back": True, "detail": rb}


def rollback(reason="requested"):
    st = load_state()
    prev = st.get("previous") or other(active())
    cur = active()
    if st.get("previous_head") and (head(prev) != st["previous_head"] or len(dirty(prev)) != st.get("previous_dirty", 0)):
        log_event("rollback-refused", to=prev, reason=reason)
        return {"ok": False, "error": f"Slot {prev} no longer holds the previous version (it was prepared again), so going back to it is refused."}
    set_active(prev)
    save_state(previous=cur, previous_head=head(cur), previous_dirty=len(dirty(cur)), probation_until=0, wd_fails=0)
    log_event("rollback", frm=cur, to=prev, reason=reason)
    _board_call("post", f"Rosie went back from slot {cur} to slot {prev}: {reason}", "note", "all")
    restart_service()
    ok = wait_healthy(prev)
    return {"ok": ok, "active": prev, "reason": reason}


def status():
    st = load_state()
    live = active()
    idle = other(live)
    return {"claimed_by": claimed(idle), "active": live, "inactive": idle, "paths": {k: str(v) for k, v in SLOT.items()}, "heads": {k: head(k) for k in SLOT if SLOT[k].exists()},
            "inactive_dirty": len(dirty(idle)), "inactive_exists": SLOT[idle].exists(), "prepared": st.get("prepared"), "last_stage": st.get("last_stage"),
            "probation_seconds_left": max(0, int(st.get("probation_until", 0) - time.time())), "previous": st.get("previous"), "events": events(5), "healthy": bool(healthz())}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        out = {"status": status, "events": events, "diff": diff, "ensure-b": ensure_b,
               "prepare": lambda: prepare(sys.argv[2] if len(sys.argv) > 2 else "update", "--force" in sys.argv),
               "commit": lambda: commit(sys.argv[2] if len(sys.argv) > 2 else "Self-update"),
               "stage": lambda: stage("--no-eval" not in sys.argv), "switch": lambda: switch("--force" in sys.argv),
               "rollback": lambda: rollback(sys.argv[2] if len(sys.argv) > 2 else "requested"),
               "claim": lambda: claim(sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "--force" else "working on it", "--force" in sys.argv),
               "release": release}[cmd]()
    except KeyError:
        out = {"error": f"unknown command {cmd}"}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))
