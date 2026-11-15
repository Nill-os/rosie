import datetime as dt
import json
import subprocess

import a0
import mind

EVERY_S = 600
ROSIE_MARK = "[Rosie turn "
MAX_USER = 4000
MAX_REPLY = 2000
MIN_WORDS = 3

DUMP = r"""
import glob, json, os
out = []
for f in sorted(glob.glob("/a0/usr/chats/*/chat.json")):
    try:
        d = json.load(open(f))
        a = (d.get("agents") or [{}])[0]
        h = json.loads(a.get("history") or "{}")
    except Exception:
        continue
    msgs = []
    for t in [*(h.get("bulks") or []), *(h.get("topics") or []), h.get("current")]:
        for m in (t or {}).get("messages") or []:
            msgs.append({"id": m.get("id"), "ai": bool(m.get("ai")), "content": m.get("content")})
    out.append({"id": d.get("id"), "created": d.get("created_at"), "msgs": msgs})
print(json.dumps(out))
"""


def _tool(content) -> tuple[str, str]:
    try:
        d = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        return "", ""
    if not isinstance(d, dict):
        return "", ""
    name = str(d.get("tool_name") or "")
    args = d.get("tool_args") if isinstance(d.get("tool_args"), dict) else {}
    return name, str(args.get("text") or "") if name == "response" else ""


def turns(chat: dict) -> list[dict]:
    out, cur = [], None
    for m in chat.get("msgs") or []:
        c = m.get("content")
        if not m.get("ai"):
            if isinstance(c, dict) and isinstance(c.get("user_message"), str):
                text = c["user_message"].strip()
                cur = None if len(text.split()) < MIN_WORDS or text.startswith(ROSIE_MARK) else {"id": m.get("id"), "user": text, "tools": set()}
            continue
        if cur is None:
            continue
        name, reply = _tool(c)
        if name == "response":
            out.append({"id": cur["id"], "user": cur["user"], "reply": reply.strip(), "tainted": bool(cur["tools"])})
            cur = None
        elif name:
            cur["tools"].add(name)
    return out


def _when(created) -> str | None:
    try:
        return mind._iso(dt.datetime.fromisoformat(str(created)).astimezone().replace(microsecond=0))
    except (TypeError, ValueError):
        return None


def dump() -> list[dict]:
    r = subprocess.run(["docker", "exec", "-i", a0.CONTAINER, "python3", "-"], input=DUMP, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"a0 chat dump failed: {(r.stderr or '').strip()[:200]}")
    data = json.loads(r.stdout or "[]")
    return data if isinstance(data, list) else []


def collect(chats: list[dict] | None = None) -> int:
    if chats is None:
        if not a0.enabled():
            return 0
        chats = dump()
    owner, n = mind._owner(), 0
    for chat in chats:
        cid = str(chat.get("id") or "")
        if not cid:
            continue
        for t in turns(chat):
            # board #746 safety-outside-text-no-memory
            body = f"{owner}: {t['user'][:MAX_USER]}" + ("" if t["tainted"] or not t["reply"] else f"\nRosie: {t['reply'][:MAX_REPLY]}")
            if mind.queue(body, "chat", f"a0:{cid}:{t['id']}", context="a chat with Rosie", mentioned_at=_when(chat.get("created"))):
                n += 1
    return n
