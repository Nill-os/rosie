"""Claude Code token use from its transcripts (~/.claude/projects/**/*.jsonl), read incrementally: each refresh() reads only the
bytes added since the last one. Each assistant message counts once (Claude Code repeats its usage on one line per content block)."""
import datetime as dt
import json
import os
import re
from pathlib import Path

FIELDS = (("input", "input_tokens"), ("output", "output_tokens"), ("cache_write", "cache_creation_input_tokens"), ("cache_read", "cache_read_input_tokens"))
MAX_AGE_DAYS = 35                    # older transcripts are skipped on the first read (they can't change today's numbers)
USAGE = re.compile(rb'"usage"\s*:\s*\{')
ROLE = re.compile(rb'"role"\s*:\s*"assistant"|"type"\s*:\s*"assistant"')
FIELD = {k: re.compile(rb'"' + src.encode() + rb'"\s*:\s*(\d+)') for k, src in FIELDS}
MSG_ID = re.compile(rb'"id"\s*:\s*"(msg_[^"]+)"')
SESSION = re.compile(rb'"sessionId"\s*:\s*"([^"]+)"')
STAMP = re.compile(rb'"timestamp"\s*:\s*"(\d{4}-\d\d-\d\dT\d\d)')
FULL_STAMP = re.compile(rb'"timestamp"\s*:\s*"([^"]{10,40})"')
TOOL = re.compile(rb'"type"\s*:\s*"tool_use"\s*,\s*"id"\s*:\s*"[^"]*"\s*,\s*"name"\s*:\s*"([^"]+)"\s*,\s*"input"\s*:\s*\{')
ARG = re.compile(rb'"(description|file_path|pattern|url|query|skill|subagent_type|command|prompt)"\s*:\s*"((?:[^"\\]|\\.){0,300})')
ARG_RANK = [b"description", b"file_path", b"pattern", b"url", b"query", b"skill", b"subagent_type", b"command", b"prompt"]
TEXT = re.compile(rb'"type"\s*:\s*"text"\s*,\s*"text"\s*:\s*"')
TITLE = re.compile(rb'"(aiTitle|customTitle)"\s*:\s*"((?:[^"\\]|\\.){1,200})"')


def _zero() -> dict:
    return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "messages": 0}


class TokenCounter:
    def __init__(self, root: str):
        self.root = Path(root)
        self.files = {}              # path -> {"offset", "ino", "ids": recent message ids, "hours": {(session, "YYYY-MM-DDTHH" UTC): totals}}
        self.activity = {}           # session -> its latest step: {"t": ISO time (UTC), "tool": "Bash" or "", "what": short text}
        self.titles = {}             # session -> {"aiTitle": Claude Code's own chat title, "customTitle": one the owner gave it (/rename)}

    def refresh(self):
        cutoff = dt.datetime.now().timestamp() - MAX_AGE_DAYS * 86400
        for p in self.root.rglob("*.jsonl"):
            try:
                st = p.stat()
            except OSError:
                continue
            f = self.files.get(p)
            if f is None:
                if st.st_mtime < cutoff:
                    continue
                f = self.files[p] = {"offset": 0, "ino": st.st_ino, "ids": [], "hours": {}}
            if st.st_ino != f["ino"] or st.st_size < f["offset"]:            # replaced or cut short: count it again from the start
                f.update(offset=0, ino=st.st_ino, ids=[], hours={})
            if st.st_size == f["offset"]:
                continue
            try:
                with open(p, "rb") as fh:                                    # one line at a time: transcripts can be hundreds of MB
                    fh.seek(f["offset"])
                    for line in fh:
                        if not line.endswith(b"\n"):                         # a half-written line: read it when it is whole
                            break
                        f["offset"] += len(line)
                        self._line(f, line)
            except OSError:
                continue
        for p in [p for p in self.files if not p.exists()]:
            del self.files[p]
        return self

    def _activity(self, line: bytes):
        """The step this line shows: a tool call (its name and description, file or command) or a written reply."""
        sid, ts = SESSION.search(line), FULL_STAMP.search(line)
        if not sid or not ts:
            return
        tool = None
        for tool in TOOL.finditer(line):
            pass
        if tool:
            args = {k: v for k, v in ARG.findall(line[tool.end():tool.end() + 4000])[:12]}
            raw = next((args[k] for k in ARG_RANK if k in args), b"")
            try:
                what = json.loads(b'"' + raw + b'"')
            except ValueError:
                what = raw.decode(errors="replace")
            step = {"tool": tool.group(1).decode(errors="replace")[:40], "what": " ".join(what.split())[:160]}
        elif TEXT.search(line):
            step = {"tool": "", "what": "Writing a reply"}
        else:
            return
        key, t = sid.group(1).decode(), ts.group(1).decode()
        if t >= self.activity.get(key, {}).get("t", ""):                   # files are read in any order: keep the newest step
            self.activity[key] = {"t": t, **step}

    def _line(self, f, line: bytes):
        """Only the few fields it needs, found without parsing the whole line (an assistant line carries the full reply text). Inside
        JSON strings quotes are escaped, so a quoted key like "usage": only matches a real key."""
        if b'Title"' in line[:400] and (t := TITLE.search(line)):          # the chat's title line (short, no usage)
            sid = SESSION.search(line)
            if sid:
                try:
                    title = json.loads(b'"' + t.group(2) + b'"')
                except ValueError:
                    title = t.group(2).decode(errors="replace")
                self.titles.setdefault(sid.group(1).decode(), {})[t.group(1).decode()] = " ".join(title.split())
            return
        m = USAGE.search(line)
        if not m or not ROLE.search(line):
            return
        self._activity(line)
        u = line[m.end():m.end() + 1200]
        if b"}" in u and b"{" not in u[:u.index(b"}")]:                     # stop at the end of the usage object (it may hold one
            u = u[:u.index(b"}")]                                            # nested object, cache_creation, which this keeps)
        mid = MSG_ID.search(line)
        if not mid:
            try:
                d = json.loads(line)
            except ValueError:
                return
            if not isinstance(d, dict) or d.get("type") != "assistant":
                return
            key = (d.get("message") or {}).get("id") or d.get("uuid")
        else:
            key = mid.group(1)
        if key in f["ids"]:                                    # a message's repeats sit next to each other: the last few ids are enough
            return
        f["ids"] = (f["ids"] + [key])[-32:]
        sid, ts = SESSION.search(line), STAMP.search(line)
        t = f["hours"].setdefault(((sid.group(1).decode() if sid else ""), (ts.group(1).decode() if ts else "")), _zero())
        for k, rx in FIELD.items():
            v = rx.search(u)
            t[k] += int(v.group(1)) if v else 0
        t["messages"] += 1

    def title(self, session: str) -> str:
        t = self.titles.get(session) or {}
        return t.get("customTitle") or t.get("aiTitle") or ""

    def _hours(self):
        for f in self.files.values():
            yield from f["hours"].items()

    def by_session(self) -> dict:
        out = {}
        for (sid, _), t in self._hours():
            s = out.setdefault(sid, _zero())
            for k, v in t.items():
                s[k] += v
        return out

    def by_day(self, tz_offset_minutes: int) -> dict:
        """Per local day. Kept per UTC hour, so an offset that isn't whole hours rounds to the hour."""
        out, shift = {}, dt.timedelta(minutes=tz_offset_minutes)
        for (_, hour), t in self._hours():
            try:
                day = (dt.datetime.fromisoformat(hour + ":00:00+00:00") + shift).strftime("%Y-%m-%d")
            except ValueError:
                continue
            s = out.setdefault(day, _zero())
            for k, v in t.items():
                s[k] += v
        return out


if __name__ == "__main__":                                              # python3 agentdeck_tokens.py: today's use
    c = TokenCounter(os.path.expanduser("~/.claude/projects")).refresh()
    print(json.dumps(c.by_day(dt.datetime.now().astimezone().utcoffset() // dt.timedelta(minutes=1)), indent=1))
