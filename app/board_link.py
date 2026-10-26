# board #714 owner-shared-board-2026-10-23
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx

import agentboard
import config

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "board_link.json"
API = "https://api.github.com"
EVERY_S = 60
OUT_KINDS = ("doing", "done", "blocked", "note", "task")
TRUSTED = ("OWNER", "COLLABORATOR", "MEMBER")  # board #714 safety-trusted-writers
MARK = re.compile(r"<!-- rosie-board (\{.*?\}) -->", re.S)
OUTBOX = re.compile(r"<!-- rosie-board-outbox (\{.*?\}) -->", re.S)
KEEP = 40
MAX_BODY = 60000
FIND_EVERY_S = 600
NAME = re.compile(r"[^a-z0-9-]+")
status = {"on": False, "last": "", "error": "", "sent": 0, "received": 0}
_token = {"value": "", "t": 0.0}


def settings() -> dict:
    cfg = config.load()
    pc = NAME.sub("-", (cfg.get("board_link_pc") or socket.gethostname()).lower()).strip("-")[:24] or "pc"
    folder = str(cfg.get("board_link_dir") or "").strip()
    return {"repo": (cfg.get("board_link_repo") or "").strip(), "issue": int(cfg.get("board_link_issue") or 1), "pc": pc,
            "dir": str(Path(folder).expanduser()) if folder else ""}


def _gh() -> str:
    found = shutil.which("gh")
    if found:
        return found
    win = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    return str(win) if win.exists() else "gh"


def token() -> str:
    if _token["value"] and time.time() - _token["t"] < 600:
        return _token["value"]
    r = subprocess.run([_gh(), "auth", "token"], capture_output=True, text=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _token.update(value=r.stdout.strip() if r.returncode == 0 else "", t=time.time())
    if not _token["value"]:
        raise RuntimeError("GitHub CLI isn't signed in on this PC (run: gh auth login)")
    return _token["value"]


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(st: dict):
    import atomic
    atomic.write_text(STATE, json.dumps(st, indent=1))


def render(p: dict, pc: str) -> str:
    meta = {"v": 1, "pc": pc, "agent": p["agent"], "kind": p["kind"], "to": p.get("to_agent") or "", "id": p["id"], "text": p["text"]}
    head = f"**{p['kind']}** · `{p['agent']}@{pc}`" + (f" → `{meta['to']}`" if meta["to"] else "")
    marker = json.dumps(meta, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
    shown = _shown(p["text"])
    return f"{head}\n\n{shown}\n\n<!-- rosie-board {marker} -->"


def parse(body: str) -> dict | None:
    m = MARK.search(body or "")
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(meta, dict) or not all(isinstance(meta.get(k), str) for k in ("pc", "agent", "kind", "to", "text")):
        return None
    return meta


def outgoing(p: dict) -> bool:
    if not agentboard.CLAUDE_RE.fullmatch(p["agent"] or "") or p["kind"] not in OUT_KINDS:
        return False
    to = p.get("to_agent") or ""
    return p["kind"] in ("doing", "done", "blocked") or to in ("", "all") or "@" in to


def incoming(meta: dict, mine: str) -> tuple | None:
    pc = NAME.sub("-", meta["pc"].lower()).strip("-")[:24]
    agent = NAME.sub("-", meta["agent"].lower()).strip("-")[:40]
    if not pc or pc == mine or not agent or meta["kind"] not in OUT_KINDS:
        return None
    to = meta["to"].lower().strip()
    if to.endswith("@" + mine):
        to = to[: -len(mine) - 1]
    elif "@" in to:
        return None
    elif to not in ("", "all"):
        to = f"{to}@{pc}"
    return f"{agent}@{pc}", meta["kind"], to


def _after(post_id: int, n=50) -> list[dict]:
    with agentboard._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM posts WHERE id > ? ORDER BY id LIMIT ?", (post_id, n))]


def _shown(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace("@", "@\u2060")


def render_outbox(posts: list[dict], pc: str) -> str:
    head = (f"**Board relay from `{pc}`.** This comment is edited in place, so GitHub sends no email for it. "
            f"<!-- rosie-board-outbox {json.dumps({'v': 2, 'pc': pc})} -->")
    parts = [render(p, pc) for p in posts]
    while parts and len(head) + sum(len(x) + 9 for x in parts) > MAX_BODY:
        parts.pop(0)
    return "\n\n---\n\n".join([head, *parts])


def outbox_pc(body: str) -> str:
    m = OUTBOX.search(body or "")
    try:
        return str(json.loads(m.group(1)).get("pc") or "") if m else ""
    except ValueError:
        return ""


def parse_all(body: str) -> list[dict]:
    out = []
    for m in MARK.finditer(body or ""):
        meta = parse(m.group(0))
        if meta:
            out.append(meta)
    return out


def _comments(client, url) -> list[dict]:
    out = []
    for page in range(1, 20):
        r = client.get(url, params={"per_page": 100, "page": page})
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < 100:
            break
    return out


# board #714 owner-no-email-spam
def sync(client=None) -> dict:
    s = settings()
    if s["dir"]:
        return folder_sync(s)
    if not s["repo"]:
        status.update(on=False, error="")
        return {"ok": False, "note": "The board link is off (Settings: board_link_repo)."}
    st = _load()
    if st.get("repo") != s["repo"] or st.get("issue") != s["issue"] or st.get("v") != 2:
        st = {"v": 2, "repo": s["repo"], "issue": s["issue"], "out": agentboard.top_id(), "outbox": None, "keep": [], "peers": {},
              "seen": {}, "found_t": 0}
    base = f"{API}/repos/{s['repo']}/issues"
    url = f"{base}/{s['issue']}/comments"
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                                                         "X-GitHub-Api-Version": "2022-11-28"})
    sent = got = 0
    try:
        if not st.get("outbox") or time.time() - float(st.get("found_t") or 0) > FIND_EVERY_S:
            for c in _comments(client, url):
                pc = outbox_pc(c.get("body") or "")
                if not pc or c.get("author_association") not in TRUSTED:
                    continue
                if pc == s["pc"]:
                    st["outbox"] = st.get("outbox") or c["id"]
                else:
                    st["peers"][pc] = c["id"]
            st["found_t"] = time.time()
        new = []
        for p in _after(int(st.get("out") or 0)):
            if outgoing(p):
                new.append({k: p[k] for k in ("id", "agent", "kind", "to_agent", "text")})
            st["out"] = max(int(st.get("out") or 0), p["id"])
        if new:
            st["keep"] = (st.get("keep") or [])[-KEEP:] + new
            st["keep"] = st["keep"][-KEEP:]
            body = render_outbox(st["keep"], s["pc"])
            if st.get("outbox"):
                client.patch(f"{base}/comments/{st['outbox']}", json={"body": body}).raise_for_status()
            else:
                r = client.post(url, json={"body": body})
                r.raise_for_status()
                st["outbox"] = (r.json() or {}).get("id")
            sent = len(new)
        _save(st)
        for pc, cid in list(st["peers"].items()):
            r = client.get(f"{base}/comments/{cid}")
            if getattr(r, "status_code", 200) == 404:
                st["peers"].pop(pc, None)
                st["found_t"] = 0
                continue
            r.raise_for_status()
            c = r.json() or {}
            if c.get("author_association") not in TRUSTED:
                continue
            metas = [m for m in parse_all(c.get("body") or "") if isinstance(m.get("id"), int)]
            last = st["seen"].get(pc)
            if last is None:
                st["seen"][pc] = max((m["id"] for m in metas), default=0)
                continue
            for meta in sorted(metas, key=lambda m: m["id"]):
                if meta["id"] <= last:
                    continue
                st["seen"][pc] = meta["id"]
                got_ = incoming(meta, s["pc"])
                if not got_:
                    continue
                agent, kind, to = got_
                res = agentboard.post(meta["text"], kind=kind, to=to, agent=agent)
                if res.get("ok"):
                    agentboard.checkin(agent=agent, task=meta["text"] if kind == "doing" else None)
                    got += 1
        _save(st)
    finally:
        if own:
            client.close()
    status.update(on=True, last=time.strftime("%H:%M"), error="", sent=status["sent"] + sent, received=status["received"] + got)
    return {"ok": True, "sent": sent, "received": got}


KEEP_DAYS = 14


def _post_file(folder: Path, meta: dict):
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{meta['id']:010d}.json"
    if final.exists():
        return
    tmp = folder / f".{meta['id']}.writing"  # board #714 why-write-then-rename
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def _read_post(f: Path) -> dict | None:
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("id"), int) or \
            not all(isinstance(meta.get(k), str) for k in ("pc", "agent", "kind", "to", "text")):
        return None
    return meta


def folder_sync(s: dict) -> dict:
    root = Path(s["dir"])
    if not root.parent.exists():
        status.update(on=False, error=f"The shared folder {root.parent} isn't there.")
        return {"ok": False, "note": f"The shared folder {root.parent} isn't there."}
    st = _load()
    if st.get("dir") != str(root) or st.get("v") != 3:
        st = {"v": 3, "dir": str(root), "out": agentboard.top_id(), "seen": {}}
    mine = root / s["pc"]
    sent = got = 0
    for p in _after(int(st.get("out") or 0)):
        if outgoing(p):
            _post_file(mine, {"v": 3, "pc": s["pc"], "agent": p["agent"], "kind": p["kind"], "to": p.get("to_agent") or "",
                              "id": p["id"], "text": p["text"], "t": time.time()})
            sent += 1
        st["out"] = max(int(st.get("out") or 0), p["id"])
    _save(st)
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in mine.glob("*.json") if mine.exists() else []:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
    for d in sorted(root.iterdir()) if root.exists() else []:
        pc = NAME.sub("-", d.name.lower()).strip("-")
        if not d.is_dir() or pc == s["pc"] or not pc:
            continue
        ids = sorted(int(f.stem) for f in d.glob("*.json") if f.stem.isdigit())
        last = st["seen"].get(pc)
        if last is None:
            st["seen"][pc] = ids[-1] if ids else 0
            continue
        for i in [i for i in ids if i > last]:
            f = d / f"{i:010d}.json"
            meta = _read_post(f)
            if meta is None:
                try:
                    stale = time.time() - f.stat().st_mtime > 600
                except OSError:
                    stale = False
                if not stale:
                    break
                st["seen"][pc] = i
                continue
            st["seen"][pc] = i
            got_ = incoming({**meta, "pc": pc}, s["pc"])
            if not got_:
                continue
            agent, kind, to = got_
            res = agentboard.post(meta["text"], kind=kind, to=to, agent=agent)
            if res.get("ok"):
                agentboard.checkin(agent=agent, task=meta["text"] if kind == "doing" else None)
                got += 1
    _save(st)
    status.update(on=True, last=time.strftime("%H:%M"), error="", sent=status["sent"] + sent, received=status["received"] + got)
    return {"ok": True, "sent": sent, "received": got, "via": "folder"}


def _loop():
    while True:
        try:
            sync()
        except Exception as e:
            status.update(error=f"{type(e).__name__}: {str(e)[:160]}")
        time.sleep(EVERY_S)


def start():
    import testguard
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    threading.Thread(target=_loop, daemon=True, name="board-link").start()


if __name__ == "__main__":
    print(json.dumps(sync(), indent=1))
