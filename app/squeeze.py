import collections
import itertools
import json
import re
import threading

KEEP = 40
PART = 6000
LONG_TEXT = 700
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
        flagged = [i for i in range(LIST_HEAD, len(x) - LIST_TAIL) if _flagged(x[i])][:5]  # board #778 why-errors-always-shown
        picks = set(range(LIST_HEAD)) | set(flagged) | set(range(len(x) - LIST_TAIL, len(x)))
        per = max(120, (budget - 120) // len(picks))
        small = {i: _shrink(x[i], per, depth + 1) for i in picks}
        used = len(_dump(list(small.values())))
        for i in range(LIST_HEAD, len(x) - LIST_TAIL):
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
    if len(text) > limit:  # board #778 quirk-deep-nesting-plain-head
        text = _dump({"result_start": full[:limit - 300], **note})
    return text


def more_result(ref: str, part: int = 1) -> dict:
    with _lock:
        full = _store.get(str(ref).strip().strip("'\""))
    if full is None:
        return {"error": "That result is no longer kept. Run the tool again with a narrower question."}
    part = max(1, int(part or 1))
    pages = max(1, -(-len(full) // PART))
    if part > pages:
        return {"error": f"There are only {pages} parts."}
    return {"ref": ref, "part": part, "parts": pages, "text": full[(part - 1) * PART:part * PART]}
