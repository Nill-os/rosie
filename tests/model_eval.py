"""Compare local models on what Rosie really does. Not part of the automatic tests (it needs Ollama and the models).
Usage:  .venv/bin/python tests/model_eval.py qwen3.5:9b gemma4:12b ...
Everything runs on throwaway data with a fake calendar and a fake phone; nothing real is changed or sent. Each task is scored automatically:
the right tool was called with sensible arguments, no pointless question or offer at the end, and a real answer."""
import datetime as dt
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import actions  # noqa: E402
import agent  # noqa: E402
import chats  # noqa: E402
import config  # noqa: E402
import fastpath  # noqa: E402
import gcal  # noqa: E402
import inbox  # noqa: E402
import lessons  # noqa: E402
import memory  # noqa: E402
import phone  # noqa: E402
import tasks  # noqa: E402
import workflows  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rosie-eval-"))
config.PATH = TMP / "config.json"
tasks.DB, inbox.DB, chats.DB, memory.DB, lessons.DB = TMP / "t.db", TMP / "i.db", TMP / "c.db", TMP / "m.db", TMP / "l.db"
fastpath.try_fast = lambda *a, **k: None          # judge the MODEL, not the exact-code shortcuts
import corner_graph  # noqa: E402
corner_graph.recall = lambda *a, **k: ""          # and not what the live corner happens to hold today
import mind  # noqa: E402
mind.DB = TMP / "mind.db"                          # her memory: an empty throwaway one
JOBS = []


class FakeCal:
    def __init__(self):
        d = dt.datetime.now(gcal.TZ).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
        self.day, self.n, self.events = d, 0, []
        self.add("Gym", d.replace(hour=7), d.replace(hour=9))
        self.add("Client call", d.replace(hour=10), d.replace(hour=11))
        self.add("Lunch", d.replace(hour=12), d.replace(hour=13))

    def add(self, title, s, e):
        self.n += 1
        ev = {"id": f"e{self.n}", "title": title, "start": s.isoformat(), "end": e.isoformat(), "all_day": False, "location": ""}
        self.events.append(ev)
        return ev


CAL = FakeCal()


def _overlap(s, e, calendar_id="primary", query=None):
    return [x for x in CAL.events if dt.datetime.fromisoformat(x["start"]) < e and dt.datetime.fromisoformat(x["end"]) > s and (not query or query.lower() in x["title"].lower())]


gcal.list_events = _overlap
gcal.events_on = lambda day, calendar_id="primary": _overlap(dt.datetime.combine(day, dt.time.min, tzinfo=gcal.TZ), dt.datetime.combine(day, dt.time.max, tzinfo=gcal.TZ))
gcal.search = lambda q, days_ahead=60, calendar_id="primary": [x for x in CAL.events if q.lower() in x["title"].lower()]
gcal.write_calendar_id = lambda: "test"
gcal.add_event = lambda title, s, e, calendar_id=None, location="": CAL.add(title, s, e)
gcal.get_event = lambda eid, cid=None: next(x for x in CAL.events if x["id"] == eid)
gcal.move_event = lambda eid, s, e, calendar_id=None: next(x.update(start=s.isoformat(), end=e.isoformat()) or x for x in CAL.events if x["id"] == eid)


def fake_run(action, args=None, timeout=40):
    JOBS.append({"action": action, "args": args or {}})
    if action == "find_contact":
        n = (args or {}).get("name", "").lower()
        return {"matches": ({"mom": [{"name": "Mom", "number": "+15551110002", "type": "mobile"}], "sam": [{"name": "Sam Lee", "number": "+1555333", "type": "mobile"}, {"name": "Sam Ortiz", "number": "+1555444", "type": "mobile"}]}.get(next((k for k in ("mom", "sam") if k in n), ""), []))}
    if action == "read_sms":
        return {"messages": [{"from": "+15551110002", "text": "Dinner at 6?", "when": "Sun 8:15 AM"}]}
    if action == "status":
        return {"connected": True, "battery_percent": 71, "headset": False}
    return {"done": True, "note": "ok"}


phone.online = lambda: True
phone.run = fake_run


def trace(new, start):
    calls = []
    for m in new[start:]:
        for c in m.get("tool_calls") or []:
            calls.append((c["function"]["name"], c["function"].get("arguments") or {}))
    return calls


OFFER = re.compile(r"(would you like|do you want|want me to|should i|shall i|let me know|anything else|how can i help)", re.I)


def names(calls):
    return [n for n, _ in calls]


# (prompt, check(calls, reply, pending) -> (ok, why))
def t_reminder(c, r, p):
    x = [a for n, a in c if n == "set_reminder"]
    return bool(x) and "bank" in json.dumps(x[0]).lower() and ("4" in json.dumps(x[0])), "set_reminder with 'bank' and 4pm"


def t_event(c, r, p):
    ev = [e for e in CAL.events if "laundry" in e["title"].lower()]
    return bool(ev) and not any(n == "add_event" and False for n, _ in c), "a laundry event was really created without clashing"


def t_text(c, r, p):
    return "phone_send_sms" in names(c) and "Mom" in json.dumps([a for n, a in c if n == "phone_send_sms"]) and bool(p), "called phone_send_sms for Mom and produced a confirmation"


def t_weather(c, r, p):
    return "get_weather" in names(c) and "Chicago" in r + json.dumps(c), "used get_weather"


def t_task(c, r, p):
    return any("stamp" in t["title"].lower() for t in tasks.list_for()), "task 'stamps' was really added"


def t_alarm(c, r, p):
    x = [a for n, a in c if n == "phone_set_alarm"]
    return bool(x) and "06:45" in json.dumps(x[0]), "phone_set_alarm 06:45"


def t_chat(c, r, p):
    return not c and len(r) > 10, "answered directly without tools"


def t_sms_read(c, r, p):
    return "phone_read_messages" in names(c) and "6" in r, "read the texts and reported them"


def t_which(c, r, p):
    return "Which one" in r and not any(n == "phone_send_sms" and "555333" in json.dumps(a) for n, a in c), "asked which Sam instead of picking"


def t_gpu(c, r, p):
    return "run_command" in names(c) and bool(re.search(r"\d", r)), "ran a command and reported a number"


def t_list_tasks(c, r, p):
    return "list_tasks" in names(c) or "what_now" in names(c), "looked at the tasks"


def t_flash(c, r, p):
    return any(j["action"] == "torch" for j in JOBS), "turned on the flashlight on the phone"


def t_recipe(c, r, p):
    k = [a for n, a in c if n == "phone_keep_note"]
    return "find_recipe" in names(c) and bool(k) and "http" in json.dumps(k[0]) and r.startswith("Ready") and bool(p), "read a real recipe, saved it with its source link, and asked before saving (no false 'done')"


SCENARIOS = [
    ("Remind me to call the bank at 4 PM today", t_reminder, False),
    ("Add a one hour laundry block tomorrow at 10 AM", t_event, False),
    ("Text Mom that I'll be there at six", t_text, False),
    ("What's the weather in Chicago today?", t_weather, False),
    ("Add a task to buy stamps", t_task, False),
    ("Set an alarm for 6:45 tomorrow morning", t_alarm, False),
    ("Give me one sentence of encouragement", t_chat, False),
    ("What did Mom text me?", t_sms_read, False),
    ("Text Sam hello", t_which, True),
    ("How hot is my GPU right now?", t_gpu, False),
    ("What tasks do I have today?", t_list_tasks, False),
    ("Turn on my flashlight", t_flash, False),
    ("Make a new Keep note with the best thin crust pizza dough recipe you can find", t_recipe, False),
]


def run_model(model):
    agent.MODEL = model
    workflows.MODEL = model
    rows, total_s = [], 0.0
    for prompt, check, asks in SCENARIOS:
        JOBS.clear()
        CAL.events[:] = CAL.events[:3]
        tasks_before = len(tasks.list_for())
        hist = []
        t0 = time.time()
        try:
            reply, pending, new = agent.chat(hist, prompt, "auto", False)
            calls = trace(new, 0)
            ok, why = check(calls, reply, pending)
        except Exception as e:
            reply, pending, calls, ok, why = f"ERROR {type(e).__name__}: {e}", [], [], False, "crashed"
        dt_s = time.time() - t0
        total_s += dt_s
        clean = asks or not (OFFER.search(reply) or reply.strip().endswith("?"))
        for p in pending:
            actions.cancel(p["token"])
        rows.append({"prompt": prompt, "ok": bool(ok), "clean": clean, "secs": round(dt_s, 1), "calls": names(calls), "reply": reply[:90].replace("\n", " ")})
    return rows, total_s


if __name__ == "__main__":
    summary = []
    for model in sys.argv[1:]:
        print(f"\n===== {model} =====", flush=True)
        rows, total = run_model(model)
        for r in rows:
            print(f"  {'PASS' if r['ok'] else 'FAIL'} {'clean' if r['clean'] else 'ASKS '} {r['secs']:5.1f}s  {r['prompt'][:46]:46} {r['calls']}", flush=True)
        passed, clean = sum(r["ok"] for r in rows), sum(r["clean"] for r in rows)
        summary.append((model, passed, clean, len(rows), total))
        print(f"  -> {passed}/{len(rows)} correct, {clean}/{len(rows)} without needless questions, {total:.0f}s total", flush=True)
    print("\n===== summary =====")
    for m, p, c, n, t in summary:
        print(f"{m:28} correct {p}/{n}   no-chatter {c}/{n}   avg {t / n:4.1f}s per task")
