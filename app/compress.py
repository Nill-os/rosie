PREFIX = "[Earlier in this chat, summarized]"


def _text(m: dict) -> str:
    c = m.get("content")
    return c if isinstance(c, str) else str(c or "")


def _chars(history: list[dict]) -> int:
    return sum(len(_text(m)) for m in history)


def _boundary(history: list[dict], keep_recent: int) -> int:
    i = max(len(history) - max(keep_recent, 0), 0)
    while 0 < i < len(history) and history[i].get("role") == "tool":
        i -= 1
    return i


def _tool_names(m: dict) -> list[str]:
    out = []
    for c in m.get("tool_calls") or []:
        name = (c.get("function") or {}).get("name")
        if name:
            out.append(name)
    return out


def _first_line(s: str, cap: int = 160) -> str:
    for line in s.splitlines():
        if line.strip():
            return line.strip()[:cap]
    return ""


def fallback(older: list[dict]) -> str:
    asked, tools = [], []
    for m in older:
        if m.get("role") == "user":
            line = _first_line(_text(m))
            if line and not line.startswith(PREFIX):
                asked.append("- " + line)
            elif line:
                asked.append(line[len(PREFIX):].strip())
        for n in _tool_names(m):
            if n not in tools:
                tools.append(n)
    parts = ["\n".join(asked)] if asked else []
    if tools:
        parts.append("Tools used: " + ", ".join(tools))
    return "\n".join(parts)


def _transcript(older: list[dict]) -> str:
    return "\n".join(f"{m.get('role', '?')}: {_text(m)}" for m in older if _text(m))


def _summary(older: list[dict], summarize) -> str:
    if callable(summarize):
        try:
            got = summarize(_transcript(older))
            if isinstance(got, str) and got.strip():
                return got.strip()
        except Exception:
            pass
    return fallback(older)


SOFT_CAP = 1500
KEEP_TOOLS = 3
STUB = "[old tool result trimmed: {n} chars]"


def prune(history: list[dict], keep_tools: int = KEEP_TOOLS, soft_cap: int = SOFT_CAP) -> list[dict]:
    idx = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    fresh = set(idx[-keep_tools:]) if keep_tools > 0 else set()
    out, changed = [], False
    for i, m in enumerate(history):
        text = _text(m)
        if m.get("role") == "tool" and i not in fresh and len(text) > soft_cap and not text.startswith(STUB[:20]):
            m = {**m, "content": STUB.format(n=len(text)) + " " + _first_line(text, 200)}
            changed = True
        out.append(m)
    return out if changed else history


def compress(history: list[dict], keep_recent: int = 8, max_chars: int = 12000, summarize=None) -> list[dict]:
    if _chars(history) <= max_chars:
        return history
    history = prune(history)
    if _chars(history) <= max_chars:
        return history
    i = _boundary(history, keep_recent)
    if i <= 0:
        return history
    older = history[:i]
    return [{"role": "user", "content": f"{PREFIX} {_summary(older, summarize)}"}] + history[i:]
