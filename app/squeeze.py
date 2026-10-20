"""Shrink a big tool result before it reaches the model, without losing it. Our own small version of the idea in Headroom
(github.com/headroomlabs-ai/headroom, Apache-2.0): keep what matters (errors, the first and last items, short fields), shorten
long text in the middle, and keep the full result here so the model can ask for the rest with more_result(ref).

Her context window is small (a 9B model on an 8 GB GPU): a raw 20,000-character page or a list of 300 events would crowd out the
conversation. Nothing leaves the PC and no model is called."""
import collections
import itertools
import json
import re
import threading

KEEP = 40                            # full results kept for more_result (oldest dropped first)
PART = 6000                          # characters per more_result page
LONG_TEXT = 700                      # a string longer than this is shortened (head ... tail)
LIST_HEAD, LIST_TAIL = 3, 1
FLAG = re.compile(r"\b(error|fail\w*|exception|warn\w*|denied|refused|missing|invalid|conflict|overdue|urgent)\b", re.I)
_store: collections.OrderedDict = collections.OrderedDict()
_ids = itertools.count(1)
_lock = threading.Lock()


def _dump(x) -> str:
    return json.dumps(x, default=str, ensure_ascii=False)


def _flagged(item) -> bool:
    return bool(FLAG.search(_dump(item)[:4000]))


def _shrink(x, budget: int, depth: int = 0):
    """A smaller copy of x that aims at `budget` characters of JSON."""
    if isinstance(x, str):
        if len(x) <= LONG_TEXT or len(x) <= budget:
            return x
        head = max(200, min(budget - 150, 1200) * 3 // 4)
        tail = max(80, head // 4)
        return f"{x[:head]} …[{len(x) - head - tail} characters cut]… {x[-tail:]}"
    if isinstance(x, dict):
        out = {k: v for k, v in x.items() if v not in (None, "", [], {})}
        if len(_dump(out)) <= budget:
            return out
        share = max(120, budget // max(1, len(out)))
        return {k: _shrink(v, share, depth + 1) for k, v in out.items()}
    if isinstance(x, list):
        if len(_dump(x)) <= budget or len(x) <= LIST_HEAD + LIST_TAIL:
            per = max(120, budget // max(1, len(x)))
            return [_shrink(v, per, depth + 1) for v in x]
        flagged = [i for i in range(LIST_HEAD, len(x) - LIST_TAIL) if _flagged(x[i])][:5]          # errors and warnings always show
        picks = set(range(LIST_HEAD)) | set(flagged) | set(range(len(x) - LIST_TAIL, len(x)))
        per = max(120, (budget - 120) // len(picks))
        small = {i: _shrink(x[i], per, depth + 1) for i in picks}
        used = len(_dump(list(small.values())))
        for i in range(LIST_HEAD, len(x) - LIST_TAIL):           # then as many more from the top as still fit
            if i in small:
                continue
            item = _shrink(x[i], per, depth + 1)
            n = len(_dump(item)) + 2
            if used + n > budget - 120:
                break
            small[i], used = item, used + n
        kept = [small[i] for i in sorted(small)]
        return kept + [f"…{len(x) - len(small)} more of {len(x)} items not shown (ask more_result for them)…"] if len(small) < len(x) else kept
    return x


def squeeze(result, limit: int = 7000) -> str:
    """The result as JSON text of at most about `limit` characters. When it had to be shrunk, the full JSON is kept and the text says
    how to get it: {"…": "shortened", "more": "more_result(ref='r7')"}."""
    full = result if isinstance(result, str) else _dump(result)
    if len(full) <= limit:
        return full
    try:
        data = json.loads(full) if isinstance(result, str) else result
    except ValueError:
        data = full
    with _lock:
        ref = f"r{next(_ids)}"
        _store[ref] = full
        while len(_store) > KEEP:
            _store.popitem(last=False)
    small = _shrink(data, limit - 200)
    note = {"shortened": f"{len(full)} characters shortened to fit; the full result is kept", "more": f"more_result(ref='{ref}')"}
    text = _dump({"result": small, **note})
    if len(text) > limit:                                    # still too big (deep nesting): plain head, the rest stays retrievable
        text = _dump({"result_start": full[:limit - 300], **note})
    return text


def more_result(ref: str, part: int = 1) -> dict:
    """The rest of a shortened tool result, PART characters at a time."""
    with _lock:
        full = _store.get(str(ref).strip().strip("'\""))
    if full is None:
        return {"error": "That result is no longer kept. Run the tool again with a narrower question."}
    part = max(1, int(part or 1))
    pages = max(1, -(-len(full) // PART))
    if part > pages:
        return {"error": f"There are only {pages} parts."}
    return {"ref": ref, "part": part, "parts": pages, "text": full[(part - 1) * PART:part * PART]}
