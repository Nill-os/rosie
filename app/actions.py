"""Safe write actions. Deletes and bulk changes become pending actions that only
the UI's Confirm button (confirm()) can execute. The model has no way to call confirm()."""
import datetime as dt
import re
import secrets
import time

import gcal
from config import load

_pending: dict[str, dict] = {}   # token -> action; lost on restart (by design)
NOT_DONE = ("error", "conflict", "refused", "not_done", "needs_confirmation", "nothing", "nothing_to_move")


def _parse(s):
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=gcal.TZ)


# The owner's rule: school classes and appointments never move (only flexible work blocks and tasks do). Saved settings keep their own
# fixed_keywords list, so classes and appointments are recognized here as well, whatever the settings say.
ALWAYS_FIXED = re.compile(r"\b(class(?:es)?|lecture|lab|exam|midterm|quiz|course|school|appt|appointment|doctor|dentist|dr\.|dr(?=\s+[A-Z]))(?![a-z])", re.I)
SCHOOL = None                                                     # words that mark the owner's own school's events


def _is_fixed(title, cfg):
    t = title.lower()
    return any(k in t for k in cfg["fixed_keywords"]) or bool(ALWAYS_FIXED.search(title)) or bool(SCHOOL and SCHOOL.search(title))


PENDING_SECONDS = 600                                             # a card nobody answered for 10 minutes can no longer be confirmed


def _queue(kind, summary, payload):
    token = secrets.token_urlsafe(8)
    _pending[token] = {"kind": kind, "summary": summary, "payload": payload, "at": time.time()}
    return {"status": "needs_confirmation", "token": token, "summary": summary}


_executors: dict = {}


def register_executor(kind, fn):
    _executors[kind] = fn


def queue_tool(name, args, summary):
    """Hold a tool call until the USER confirms it in the UI (the model cannot confirm)."""
    return _queue("tool", summary, {"name": name, "args": args})


def _when(a, b):
    return f"{a.strftime('%a %b %d %H:%M')}-{b.strftime('%H:%M')}"


def add_event(title, start, end, location="", double_book=False, _confirmed=False):
    s, e = _parse(start), _parse(end)
    if e <= s:
        return {"status": "error", "message": "The end must be after the start."}
    for ex in gcal.list_events(s - dt.timedelta(minutes=5), s + dt.timedelta(minutes=5), gcal.write_calendar_id()):         # already there? do not add it twice
        if not ex["all_day"] and ex["title"].strip().lower() == title.strip().lower():
            return {"status": "done", "event": ex, "note": "That event was already on the calendar, so I did not add it again."}
    clash = gcal.conflicts(s, e)
    if clash and not double_book:
        return {"status": "conflict", "message": "NOT added: that time overlaps existing events. Do NOT ask the user: immediately call add_event again with the FIRST entry of free_slots (same length), then tell them in one sentence what you booked and why it moved.", "conflicts": clash,
                "free_slots": gcal.free_slots(s.date(), max(15, int((e - s).total_seconds() // 60)))}
    if clash and not _confirmed:
        return queue_tool("add_event", {"title": title, "start": start, "end": end, "location": location, "double_book": True, "_confirmed": True},
                          f"Add '{title}' {_when(s, e)} even though it overlaps: " + "; ".join(c["title"] for c in clash))
    return {"status": "done", "event": gcal.add_event(title, s, e, location=location)}


def resolve_event(ref):
    """Turn what the model gave (a real id, or a title/description) into a real event on the calendar we write to. Returns (event, error)."""
    cal = gcal.write_calendar_id()
    try:
        return gcal.get_event(ref, cal), None
    except Exception:
        pass
    now = dt.datetime.now(gcal.TZ)
    r = ref.lower()
    cands = [e for e in gcal.list_events(now - dt.timedelta(days=1), now + dt.timedelta(days=60), cal) if e["title"].lower() in r or r in e["title"].lower()]
    if len(cands) == 1:
        return cands[0], None
    if cands:
        return None, {"status": "error", "message": "More than one event matches. Ask which one, or use the exact id.", "candidates": [{"id": c["id"], "title": c["title"], "start": c["start"]} for c in cands[:6]]}
    return None, {"status": "error", "message": f"I could not find an event matching '{ref[:60]}' on the calendar I can change ({'your main calendar' if cal == 'primary' else 'the test calendar'}). Look it up with get_schedule or search_events and use its real id."}


def move_event(event_id, new_start, new_end, double_book=False, _confirmed=False, ask_first=False):
    ev, err = resolve_event(event_id)
    if err:
        return err
    if _is_fixed(ev["title"], load()):                        # all-day ones too (an exam day)
        return {"status": "refused", "message": f"NOT moved: '{ev['title']}' is a class or an appointment, and those never move (the owner's rule). "
                                                "Move flexible work blocks or tasks around it instead; if the owner wants it changed, they do that in Google Calendar."}
    s, e = _parse(new_start), _parse(new_end)
    if e <= s:
        return {"status": "error", "message": "The end must be after the start."}
    clash = gcal.conflicts(s, e, exclude_id=ev["id"])
    if ask_first and not _confirmed:                          # Agent Zero: the owner sees the real title, the time and any overlap
        return queue_tool("move_event", {"event_id": ev["id"], "new_start": new_start, "new_end": new_end, "double_book": True, "_confirmed": True},
                          f"Move '{ev['title']}' to {_when(s, e)}" + (" even though it overlaps: " + "; ".join(c["title"] for c in clash) if clash else ""))
    if clash and not double_book:
        return {"status": "conflict", "message": f"NOT moved: '{ev['title']}' would overlap existing events. Do NOT ask the user: immediately call move_event again with the FIRST entry of free_slots (same length), then tell them in one sentence what you did.", "conflicts": clash,
                "free_slots": gcal.free_slots(s.date(), max(15, int((e - s).total_seconds() // 60)), exclude_id=ev["id"])}
    if clash and not _confirmed:
        return queue_tool("move_event", {"event_id": ev["id"], "new_start": new_start, "new_end": new_end, "double_book": True, "_confirmed": True},
                          f"Move '{ev['title']}' to {_when(s, e)} even though it overlaps: " + "; ".join(c["title"] for c in clash))
    moved = gcal.move_event(ev["id"], s, e)
    return {"status": "done", "event": moved, "verified": "Google returned this new time: " + moved["start"]}


def delete_event(event_id, title="this event"):
    ev, err = resolve_event(event_id)                              # the card must name the REAL event, never a title the model made up
    if err or not ev:
        return err or {"status": "error", "message": "I could not find that event."}
    return _queue("delete", f"Delete event: {ev['title']} ({ev['start'][:16].replace('T', ' ')})", {"event_id": ev["id"]})


def shift_day(minutes_late: int, from_time=None, day=None, ask_first=False):
    """Push every flexible event at/after from_time (default: now) later by N minutes.
    Fixed events (appointments etc.) are left alone and reported."""
    cfg = load()
    cal = gcal.write_calendar_id()
    now = dt.datetime.now(gcal.TZ)
    day = dt.date.fromisoformat(day) if day else now.date()
    cutoff = _parse(from_time) if from_time else max(now, dt.datetime.combine(day, dt.time.min, tzinfo=gcal.TZ))
    delta = dt.timedelta(minutes=minutes_late)
    moves, skipped = [], []
    for e in gcal.events_on(day, cal):
        if e["all_day"] or _parse(e["start"]) < cutoff:
            continue
        if _is_fixed(e["title"], cfg):
            skipped.append(e["title"])
            continue
        moves.append({"id": e["id"], "title": e["title"],
                      "start": (_parse(e["start"]) + delta).isoformat(),
                      "end": (_parse(e["end"]) + delta).isoformat()})
    if not moves:
        return {"status": "nothing_to_move", "kept_fixed": skipped}
    if any(_parse(m["start"]).astimezone(gcal.TZ).date() != day for m in moves):   # skill audit 2026-10-23: "push everything back a day";
        return {"status": "error", "message": "That would push events into another day. shift_day only moves events later on the "   # a block
                "same day; offer to move specific items with move_event."}                       # may still END after midnight
    clashes = []
    for ev in gcal.events_on(day, cal):
        if ev["all_day"] or not _is_fixed(ev["title"], cfg):
            continue
        fs, fe = _parse(ev["start"]), _parse(ev["end"])
        clashes += [f"{m['title']} overlaps {ev['title']}" for m in moves
                    if _parse(m["start"]) < fe and _parse(m["end"]) > fs]
    lines = [f"{m['title']} -> {_parse(m['start']).astimezone(gcal.TZ):%H:%M}" for m in moves]   # local time, whatever the event's offset
    summary = f"Shift {len(moves)} event(s) by {minutes_late} min: " + "; ".join(lines)
    if skipped:
        summary += f" (not moved, fixed: {', '.join(skipped)})"
    if clashes:
        summary += f" WARNING: {'; '.join(clashes)}"
    if ask_first or len(moves) > cfg["max_changes_without_confirm"] or clashes:   # Agent Zero always gets this full card
        return _queue("shift", summary, {"moves": moves, "calendar": cal})
    for m in moves:
        gcal.move_event(m["id"], _parse(m["start"]), _parse(m["end"]), cal)
    return {"status": "done", "summary": summary}


def confirm(token):
    a = _pending.pop(token, None)
    if not a or time.time() - a.get("at", time.time()) > PENDING_SECONDS:
        return {"status": "error", "message": "Nothing waiting for that confirmation (expired or already used)."}
    p = a["payload"]
    done = 0
    try:
        if a["kind"] == "delete":
            gcal.delete_event(p["event_id"])
        elif a["kind"] == "tool":
            res = _executors["tool"](p["name"], p["args"])
            msg = res.get("error") if isinstance(res, dict) and res.get("error") else None
            if msg is None and isinstance(res, dict) and res.get("status") in NOT_DONE:   # a clash, a refusal or a second card: not done
                msg = str(res.get("message") or res.get("summary") or f"Not done ({res['status']}).")
            return {"status": "error" if msg else "done", "message": msg, "summary": a["summary"], "result": str(res)[:400]}
        elif a["kind"] == "shift":
            for m in p["moves"]:
                gcal.move_event(m["id"], _parse(m["start"]), _parse(m["end"]), p["calendar"])
                done += 1
    except Exception as e:                                         # report it plainly instead of a 500 and a vanished card
        extra = f" ({done} of {len(p['moves'])} moves were made before it stopped)" if a["kind"] == "shift" and done else ""
        return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:160]}{extra}", "summary": a["summary"]}
    return {"status": "done", "summary": a["summary"]}


def cancel(token):
    _pending.pop(token, None)
    return {"status": "cancelled"}
