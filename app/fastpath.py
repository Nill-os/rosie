"""Exact handling for the commands used most. A small local model is unreliable at simple, frequent jobs, so these are matched by code and done directly;
anything that does not clearly match falls through to the AI. Replies state what was done with the key details (no follow-up question)."""
import datetime as dt
import re

import actions
import inbox
import phone
import tasks
import workflows
import timefmt

R = re.I


def _clean(s: str) -> str:
    return s.strip().strip(".!?,;:").strip()


def _time_words(s: str):
    """Split '... at 4 pm' / 'in 20 minutes' / 'tomorrow at 9' off the end of a sentence. Returns (task_text, when_text) or (s, '')."""
    m = re.search(r"\b((?:in \d+(?:\.\d+)? ?(?:minutes?|mins?|hours?|hrs?|days?))|(?:(?:tomorrow|tonight|today|this (?:morning|afternoon|evening)|monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?: (?:at|around))? ?\d{0,2}(?::\d{2})? ?(?:am|pm)?)|(?:at \d{1,2}(?::\d{2})? ?(?:am|pm)?(?: (?:tomorrow|today|tonight))?))\s*$", s, R)
    if m:
        return s[:m.start()].strip(" ,"), m.group(1).strip()
    return s, ""


def _lead_time(s: str):
    """'at 4pm remind me to ...' style: time words first."""
    m = re.match(r"^(in \d+ ?(?:minutes?|mins?|hours?|hrs?)|at \d{1,2}(?::\d{2})? ?(?:am|pm)?|tomorrow(?: at \d{1,2}(?::\d{2})? ?(?:am|pm)?)?)[, ]+(.*)$", s, R)
    return (m.group(2), m.group(1)) if m else (s, "")


def _phone_ok() -> bool:
    return phone.online()


def _result(reply, tool=None, args=None, result=None):
    return {"reply": reply, "pending": [], "tool": tool, "args": args or {}, "result": result}


def try_fast(text: str, now: dt.datetime | None = None):
    """Returns {'reply', 'pending', ...} when the request clearly matches a simple command and was done, else None."""
    t = _clean(text)
    low = t.lower()
    if not t or len(t) > 220:
        return None

    # ---- time and date
    if re.fullmatch(r"(what(?:'s| is) the time|what time is it(?: right now| now)?|tell me the time|time)", low):
        n = now or dt.datetime.now()
        return _result(f"It's {n.strftime(timefmt.nopad('%-I:%M %p'))}.")
    if re.fullmatch(r"(what(?:'s| is) (?:the )?(?:date|day)(?: today)?|what day is it(?: today)?|what(?:'s| is) today(?:'s date)?)", low):
        n = now or dt.datetime.now()
        return _result(f"It's {n.strftime(timefmt.nopad('%A, %B %-d'))}.")

    # ---- reminders
    m = re.match(r"^(?:please )?(?:remind me|set (?:a )?reminder|remember(?= to )) (?:to |that |about )?(.+)$", t, R)
    if m:
        body = m.group(1)
        what, when = _time_words(body)
        if not when:
            what, when = _lead_time(body)
        if what and when:
            try:
                r = inbox.add_reminder(what, when)
            except ValueError as e:
                return _result(str(e))
            due = dt.datetime.strptime(r["due"], "%A %Y-%m-%d %H:%M")
            return _result(f"Reminder set for {due.strftime(timefmt.nopad('%A %-I:%M %p'))}: {what}.", "set_reminder", {"text": what, "when": when}, r)

    # ---- timers and alarms (phone)
    m2 = re.match(r"^(?:set (?:a |an )?)?timer (?:for )?(\d+(?:\.\d+)?) ?(minutes?|mins?|hours?|hrs?|seconds?|secs?)$", low) or re.match(r"^(?:set (?:a |an )?)?(\d+(?:\.\d+)?)[ -](minutes?|mins?|hours?|hrs?)[ -]?timer$", low)
    if m2 and _phone_ok():
        n, unit = float(m2.group(1)), m2.group(2)
        mins = n * 60 if unit.startswith(("h")) else n / 60 if unit.startswith("s") else n
        r = phone.timer(mins, "")
        if r.get("error"):
            return _result(r["error"])
        return _result(f"Timer set for {m2.group(1)} {unit}.", "phone_set_timer", {"minutes": mins}, r)
    m = re.match(r"^(?:set (?:an? )?)?alarm (?:for |at )?(\d{1,2})(?::(\d{2}))? ?(am|pm)?(?: (?:tomorrow|tomorrow morning|in the morning|this morning))?$", low)
    if m and _phone_ok():
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        r = phone.alarm(f"{h:02d}:{mi:02d}", "")
        if r.get("error"):
            return _result(r["error"])
        shown = dt.time(h, mi).strftime(timefmt.nopad("%-I:%M %p"))
        return _result(f"Alarm set for {shown}.", "phone_set_alarm", {"time_24h": f"{h:02d}:{mi:02d}"}, r)

    # ---- tasks
    m = re.match(r"^(?:please )?(?:add|put|create) (?:a )?(?:new )?task(?: to| called| named|:)? (.+)$", t, R) or re.match(r"^(?:please )?(?:add|put) (.+?) (?:to|on) (?:my )?(?:to-?do|task)s?(?: list)?$", t, R)
    if m:
        body = m.group(1)
        what, when = _time_words(body)
        if what:
            if when.startswith("at"):
                return None                                       # a clock time is not a task date: let the assistant decide (it may be a reminder)
            due = workflows.resolve_due(when) if when else None
            r = tasks.add(what, due)
            return _result(f"Task added: {what}" + (f", due {due}." if due else "."), "add_task", {"title": what, "due": due}, r)

    # ---- Google Keep note
    m = re.match(r"^(?:please )?(?:add|put|save|write|make) (.+?) (?:to|in|into|on) (?:a |my )?(?:new )?(?:google )?keep(?: note| list)?$", t, R) or re.match(r"^(?:please )?(?:note to self|keep note|add a keep note)[:,]? (.+)$", t, R)
    if m and _phone_ok():
        r = phone.keep_note("", m.group(1))
        if r.get("error"):
            return _result(r["error"])
        return _result(f"Saved to Keep: {m.group(1)}.", "phone_keep_note", {"text": m.group(1)}, r)

    # ---- flashlight, volume, music, apps (phone)
    if _phone_ok():
        m = re.match(r"^(?:turn |switch )(on|off) (?:the |my )?(?:flash ?light|torch)$", low) or re.match(r"^(?:flash ?light|torch) (on|off)$", low)
        if m:
            r = phone.torch(m.group(1) == "on")
            return _result(r["error"] if r.get("error") else f"Flashlight {m.group(1)}.", "phone_torch", {"on": m.group(1) == "on"}, r)
        m = re.match(r"^(?:turn (?:it |the volume )?|volume )(up|down)$", low) or re.match(r"^(?:make it )(louder|quieter)$", low)
        if m:
            d = {"louder": "up", "quieter": "down"}.get(m.group(1), m.group(1))
            r = phone.volume(d)
            return _result(r["error"] if r.get("error") else f"Volume {d}.", "phone_volume", {"direction": d}, r)
        m = re.match(r"^(?:set )?volume (?:to )?(\d{1,3}) ?(?:percent|%)?$", low)
        if m and int(m.group(1)) <= 100:
            r = phone.volume("", int(m.group(1)))
            return _result(r["error"] if r.get("error") else f"Volume set to {m.group(1)} percent.", "phone_volume", {"percent": int(m.group(1))}, r)
        m = re.match(r"^(pause|play|resume|skip|next|previous|stop)(?: the)?(?: music| song| track| it)?$", low)
        if m:
            a = {"resume": "play", "skip": "next"}.get(m.group(1), m.group(1))
            r = phone.music(a)
            return _result(r["error"] if r.get("error") else {"play": "Playing.", "pause": "Paused.", "next": "Next track.", "previous": "Previous track.", "stop": "Stopped."}[a], "phone_music", {"action": a}, r)
        m = re.match(r"^(?:open|launch|start) (?:the )?([a-z0-9 .+&'-]{2,30}?)(?: app)?$", low)
        if m and m.group(1) not in ("it", "that", "this", "a", "the", "my", "notes", "calendar", "mail", "email", "terminal", "project", "the terminal"):
            r = phone.open_app(m.group(1))
            if r.get("error"):
                return None                                    # not a phone app: let the AI decide (it may mean something on the PC)
            return _result(r.get("note", f"Opened {m.group(1)}."), "phone_open_app", {"name": m.group(1)}, r)

    # ---- texts and calls (they still go through confirmation: a card, a read-back, or the cancel window)
    PRONOUNS = {"me", "him", "her", "them", "us", "everyone", "everybody", "you", "myself", "it", "that", "this"}
    m = re.match(r"^(?:please )?(?:text|message|sms|send (?:a )?(?:text|message) to) (.+?) (?:saying|that says|and say|to say|that|with the message)[:,]? (.+)$", t, R) or re.match(r"^(?:please )?(?:text|message|sms) (\w+(?: \w+)?)[:,] (.+)$", t, R) \
        or re.match(r"^(?:please )?tell (\w+(?: \w+)?) (?:that|to) (.+)$", t, R)
    if m and _phone_ok():
        who, msg = _clean(m.group(1)), m.group(2).strip()
        if who and msg and len(who.split()) <= 3 and who.lower() not in PRONOUNS and not who.lower().startswith(("me ", "my ")):
            return _wrap_confirm(phone.send_sms(who, _first_person(msg)))
    m = re.match(r"^(?:please )?(?:call|phone|ring|dial) (.+)$", t, R)
    if m and _phone_ok() and len(m.group(1).split()) <= 3 and not re.search(r"\b(back|later|tomorrow|tonight|today|about|if|when|at|in)\b", m.group(1), R):
        return _wrap_confirm(phone.call(_clean(m.group(1))))
    return None


def _first_person(msg: str) -> str:
    """'tell Mom I'll be late' -> the message is already in first person; leave the words as the user said them."""
    return msg[0].upper() + msg[1:] if msg else msg


def _wrap_confirm(q: dict):
    """A tool that asks for confirmation (or asks 'which one') returns the same shapes the AI path uses."""
    if q.get("ask_user"):
        return _result(q["question"])
    if q.get("error"):
        return _result(q["error"])
    if q.get("status") == "needs_confirmation":
        out = {"reply": q["summary"] + ".", "pending": [{"token": q["token"], "summary": q["summary"], **({"auto": True} if q.get("auto") else {})}], "tool": "confirm", "args": {}, "result": q}
        return out
    return _result(str(q))
