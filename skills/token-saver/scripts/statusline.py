#!/usr/bin/env python3
"""Claude Code status line: model | context used | tokens token-saver kept out of context today."""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "..", "ledger.jsonl")


def ctx_pct(d):
    cw = d.get("context_window") or {}
    used = cw.get("total_input_tokens")
    if isinstance(used, int) and isinstance(cw.get("used_percentage"), (int, float)):
        return f"{used / 1000:.0f}k ({cw['used_percentage']:.0f}%)"
    for k in ("used_percentage", "used_percent", "percentage"):
        if isinstance(cw.get(k), (int, float)):
            return f"{cw[k]:.0f}%"
    used, size = cw.get("total_input_tokens") or cw.get("used_tokens"), cw.get("context_window_size") or cw.get("size")
    if used and size:
        return f"{100 * used / size:.0f}%"
    return "?"


def saved_today():
    today, total, rosie = time.strftime("%Y-%m-%d"), 0, 0
    try:
        with open(LEDGER) as f:
            for line in f:
                r = json.loads(line)
                if r.get("ts", "").startswith(today):
                    total += r.get("tokens_offloaded", 0) - r.get("tokens_returned", 0)
                    rosie += r.get("cmd") in ("sum", "ask", "draft", "hook-sum", "hook-read-sum")
    except (OSError, ValueError):
        pass
    return (f"{total / 1000:.0f}k" if total >= 1000 else str(total)) + f" tok | Rosie {rosie} today"


try:
    d = json.load(sys.stdin)
except ValueError:
    d = {}
model = (d.get("model") or {}).get("display_name", "Claude")
hit = (d.get("prompt_cache") or {}).get("hit_ratio")
cache = f" | cache {hit * 100:.0f}%" if isinstance(hit, (int, float)) else ""
print(f"{model} | context {ctx_pct(d)}{cache} | kept out today: {saved_today()}")
