import json
from pathlib import Path

EXPECT = {
    "Remind me to call the bank": ("set_reminder", ("text", "when"), ("bank", "4")),
    "Text Mom": ("phone_send_sms", ("to", "text"), ("Mom",)),
    "What's the weather": ("get_weather", (), ("Chicago",)),
    "Set an alarm": ("phone_set_alarm", ("time_24h",), ("06:45",)),
    "What did Mom text": ("phone_read_messages", (), ()),
    "How hot is my GPU": ("run_command", ("command",), ()),
    "Make a new Keep note": ("phone_keep_note", ("text",), ("http",)),
}
METRICS = ("tool_name", "args_parse", "required_keys", "args_match")


def _expected(prompt):
    for head, spec in EXPECT.items():
        if prompt.startswith(head):
            return spec
    return None


def parse_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
        except ValueError:
            return None
        return v if isinstance(v, dict) else None
    return None


def score_row(prompt, calls):
    spec = _expected(prompt)
    if not spec:
        return None
    tool, required, terms = spec
    mine = [(n, a) for n, a in calls if n == tool]
    out = {"tool_name": bool(mine), "args_parse": False, "required_keys": False, "args_match": False}
    if not mine:
        return out
    args = parse_args(mine[0][1])
    out["args_parse"] = args is not None
    if args is None:
        return out
    out["required_keys"] = all(args.get(k) not in (None, "", []) for k in required)
    blob = json.dumps(args).lower()
    out["args_match"] = all(t.lower() in blob for t in terms)
    return out


def summarize(rows):
    scored = [r["metrics"] for r in rows if r.get("metrics")]
    return {m: [sum(1 for s in scored if s[m]), len(scored)] for m in METRICS}


def format_counts(counts):
    return "  metrics: " + "   ".join(f"{m} {a}/{b}" for m, (a, b) in counts.items())


def append_history(path, model, passed, clean, total, counts, secs, when):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"at": when, "model": model, "correct": passed, "of": total, "clean": clean, "secs": round(secs, 1),
            "metrics": {m: {"ok": a, "of": b} for m, (a, b) in counts.items()}}
    with path.open("a") as f:
        f.write(json.dumps(line) + "\n")
    return line
