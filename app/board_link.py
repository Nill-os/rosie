"""Link this PC's agent board with another PC's through one GitHub issue (owner 2026-10-23: "i want a shared board between you two so you
know when we are working on stuff" and "deligate tasks to her claude to help"). Both PCs' Claude sessions then see each other's doing /
done / blocked posts on their own board, and can leave notes and tasks for each other.

Each relayed post is one comment on the issue (in a private repo both owners can write to). This PC's Claude-session posts go out; Rosie's
and Agent Zero's stay home (they can hold the owner's personal things). Comments from the other PC come in as posts from
"<agent>@<pc>", and only from the repo's owner or its collaborators. Settings: board_link_repo ("owner/name"), board_link_issue (number),
board_link_pc (this PC's short name). GitHub access is the GitHub CLI's login on this PC (`gh auth token`); nothing is stored here."""
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
TRUSTED = ("OWNER", "COLLABORATOR", "MEMBER")                     # who may write to the board from GitHub
MARK = re.compile(r"<!-- rosie-board (\{.*?\}) -->", re.S)
NAME = re.compile(r"[^a-z0-9-]+")
status = {"on": False, "last": "", "error": "", "sent": 0, "received": 0}
_token = {"value": "", "t": 0.0}


def settings() -> dict:
    cfg = config.load()
    pc = NAME.sub("-", (cfg.get("board_link_pc") or socket.gethostname()).lower()).strip("-")[:24] or "pc"
    return {"repo": (cfg.get("board_link_repo") or "").strip(), "issue": int(cfg.get("board_link_issue") or 1), "pc": pc}


def _gh() -> str:
    found = shutil.which("gh")
    if found:
        return found
    win = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    return str(win) if win.exists() else "gh"


def token() -> str:
    """The GitHub CLI's token for this PC's login, asked again every 10 minutes (a re-login changes it)."""
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
    """One board post as a comment: readable on GitHub, with the exact fields in a hidden marker for the other PC."""
    meta = {"v": 1, "pc": pc, "agent": p["agent"], "kind": p["kind"], "to": p.get("to_agent") or "", "id": p["id"], "text": p["text"]}
    head = f"**{p['kind']}** · `{p['agent']}@{pc}`" + (f" → `{meta['to']}`" if meta["to"] else "")
    marker = json.dumps(meta, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")   # never ends the HTML comment early
    shown = p["text"].replace("&", "&amp;").replace("<", "&lt;")             # an "<!--" in the text can't hide the rest on GitHub
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
    """A post that goes to the other PC: from one of this PC's Claude sessions, a status or a note/task meant for everyone or for the
    other PC. A note to a session here stays here."""
    if not agentboard.CLAUDE_RE.fullmatch(p["agent"] or "") or p["kind"] not in OUT_KINDS:
        return False
    to = p.get("to_agent") or ""
    return p["kind"] in ("doing", "done", "blocked") or to in ("", "all") or "@" in to


def incoming(meta: dict, mine: str) -> tuple | None:
    """(agent, kind, to) for a post from the other PC on this board, or None to leave it out."""
    pc = NAME.sub("-", meta["pc"].lower()).strip("-")[:24]
    agent = NAME.sub("-", meta["agent"].lower()).strip("-")[:40]
    if not pc or pc == mine or not agent or meta["kind"] not in OUT_KINDS:
        return None
    to = meta["to"].lower().strip()
    if to.endswith("@" + mine):
        to = to[: -len(mine) - 1]                                   # to one of this PC's sessions (or claude: all of them)
    elif "@" in to:
        return None                                                  # meant for a third PC
    elif to not in ("", "all"):
        to = f"{to}@{pc}"                                            # to a session on its own PC: shown here, but not delivered to anyone
    return f"{agent}@{pc}", meta["kind"], to


def _after(post_id: int, n=50) -> list[dict]:
    """The next posts after this one, oldest first (a long gap is sent over several runs, none skipped)."""
    with agentboard._db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM posts WHERE id > ? ORDER BY id LIMIT ?", (post_id, n))]


def sync(client=None) -> dict:
    s = settings()
    if not s["repo"]:
        status.update(on=False, error="")
        return {"ok": False, "note": "The board link is off (Settings: board_link_repo)."}
    st = _load()
    if st.get("repo") != s["repo"] or st.get("issue") != s["issue"]:      # a new link: start from now, never flood old posts
        st = {"repo": s["repo"], "issue": s["issue"], "out": agentboard.top_id(),
              "since": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "seen": []}
    url = f"{API}/repos/{s['repo']}/issues/{s['issue']}/comments"
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
                                                         "X-GitHub-Api-Version": "2022-11-28"})
    sent = got = 0
    try:
        for p in _after(int(st.get("out") or 0)):
            if outgoing(p):
                client.post(url, json={"body": render(p, s["pc"])}).raise_for_status()
                sent += 1
            st["out"] = max(int(st.get("out") or 0), p["id"])
            _save(st)                                                # a post is never sent twice, even if a later one fails
        r = client.get(url, params={"since": st["since"], "per_page": 100})
        r.raise_for_status()
        seen = set(st.get("seen") or [])
        for c in r.json():
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            st["since"] = max(st["since"], c.get("created_at") or st["since"])
            meta = parse(c.get("body") or "")
            if not meta or c.get("author_association") not in TRUSTED:
                continue
            got_ = incoming(meta, s["pc"])
            if not got_:
                continue
            agent, kind, to = got_
            res = agentboard.post(meta["text"], kind=kind, to=to, agent=agent)
            if res.get("ok"):
                agentboard.checkin(agent=agent, task=meta["text"] if kind == "doing" else None)
                got += 1
        st["seen"] = sorted(seen)[-300:]
        _save(st)
    finally:
        if own:
            client.close()
    status.update(on=True, last=time.strftime("%H:%M"), error="", sent=status["sent"] + sent, received=status["received"] + got)
    return {"ok": True, "sent": sent, "received": got}


def _loop():
    while True:
        try:
            sync()
        except Exception as e:                                       # offline, signed out, GitHub down: say so and try again later
            status.update(error=f"{type(e).__name__}: {str(e)[:160]}")
        time.sleep(EVERY_S)


def start():
    """Server start-up: relay the board every minute while a link is set. Never under tests or in a sandbox copy."""
    import testguard
    if testguard.active() or os.environ.get("ROSIE_SANDBOX"):
        return
    threading.Thread(target=_loop, daemon=True, name="board-link").start()


if __name__ == "__main__":
    print(json.dumps(sync(), indent=1))
