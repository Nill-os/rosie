# board #762 safety-texts-calls-need-ok
import queue
import threading
import time
import uuid

import actions

_cmds: "queue.Queue[dict]" = queue.Queue()
_results: dict = {}
_lock = threading.Lock()
last_seen = 0.0


def online() -> bool:
    return time.time() - last_seen < 45


def poll(wait=25):
    global last_seen
    last_seen = time.time()
    try:
        c = _cmds.get(timeout=wait)
    except queue.Empty:
        last_seen = time.time()
        return None
    last_seen = time.time()
    if c.get("deadline", 1e18) < time.time():  # board #762 why-never-run-late
        return None
    c.pop("deadline", None)
    return c


def result(cmd_id, ok, data):
    with _lock:
        ev = _results.get(cmd_id)
        if ev:
            ev["ok"], ev["data"] = ok, data
            ev["event"].set()


def run(action, args=None, timeout=40):
    if not online():
        return {"error": "The phone is not connected right now. Open the Rosie app on the phone (Settings > Phone control must be on), and make sure Tailscale is connected."}
    cid = uuid.uuid4().hex[:10]
    ev = {"event": threading.Event(), "ok": False, "data": None}
    with _lock:
        _results[cid] = ev
    _cmds.put({"id": cid, "action": action, "args": args or {}, "deadline": time.time() + timeout})
    got = ev["event"].wait(timeout)
    with _lock:
        _results.pop(cid, None)
    if not got:
        return {"error": f"The phone did not answer in {timeout} seconds."}
    return ev["data"] if ev["ok"] else {"error": ev["data"] if isinstance(ev["data"], str) else (ev["data"] or {}).get("error", "The phone could not do that.")}


def phone_status():
    return run("status")


def find_contact(name):
    return run("find_contact", {"name": name})


def keep_note(title="", text=""):
    r = run("keep_note", {"title": title, "text": text})
    if isinstance(r, dict) and not r.get("error"):
        r = {**r, "note": "Created a NEW Keep note. This cannot append to or edit an existing note or list."}
    return r


def alarm(time_24h, label=""):
    return run("alarm", {"time": time_24h, "label": label})


def timer(minutes, label=""):
    return run("timer", {"minutes": minutes, "label": label})


def open_app(name):
    return run("open_app", {"name": name})


def music(action):
    return run("music", {"action": action})


def torch(on=True):
    return run("torch", {"on": bool(on)})


def volume(direction="", percent=None):
    return run("volume", {"direction": direction, "percent": percent})


def navigate(place):
    return run("navigate", {"place": place})


def search(query):
    return run("search", {"query": query})


def read_messages(count=5, from_who=""):
    return run("read_sms", {"count": int(count or 5), "from": from_who})


user_text = ""  # board #762 safety-raw-number-said
_names: dict = {}


def _one_contact(to):
    if any(ch.isdigit() for ch in to):
        digits = "".join(ch for ch in to if ch.isdigit())
        said = "".join(ch for ch in user_text if ch.isdigit())
        if to in _names or (len(digits) >= 7 and digits[-7:] in said):
            return {"name": _names.get(to, to), "number": to}, None
        return None, {"error": "Do not use a phone number you looked up. Call again with the person's NAME as the user said it; I resolve the contact and ask which one if needed."}
    found = find_contact(to)
    if found.get("error"):
        return None, found
    m = found.get("matches", [])
    if not m:
        return None, {"error": f"I could not find '{to}' in the phone's contacts."}
    people = {x["name"] for x in m}
    if len(people) > 1:
        q = "Which one: " + " or ".join(sorted(people)[:4]) + "?"
        return None, {"error": q, "ask_user": True, "question": q}
    mobile = [x for x in m if x.get("type") == "mobile"] or m
    _names[mobile[0]["number"]] = mobile[0]["name"]
    return mobile[0], None


force_ask = False  # board #762 safety-outside-content-ask
import threading as _threading
_here = _threading.local()  # board #762 fix-thread-local-flag


def forced() -> bool:
    return force_ask or bool(getattr(_here, "ask", False))


def _auto_text() -> bool:
    import config
    if forced():
        return False
    mode = config.load().get("sms_confirm", "ask")
    if mode == "always":
        return True
    if mode == "headphones":
        st = phone_status()
        return bool(st.get("headset"))
    return False


def send_sms(to, text, _confirmed=False):
    if _confirmed:
        return run("sms", {"to": to, "text": text})
    c, err = _one_contact(to)
    if err:
        return err
    q = actions.queue_tool("phone_send_sms", {"to": c["number"], "text": text, "_confirmed": True}, f"Text {c['name']}: \"{text[:200]}\"")
    q["auto"] = _auto_text()
    return q


def call(to, _confirmed=False):
    if _confirmed:
        return run("call", {"to": to})
    import config
    mode = "ask" if forced() else config.load().get("call_confirm", "announce")
    c, err = _one_contact(to)
    if err:
        return err
    if mode == "now":
        r = run("call", {"to": c["number"]})
        return r if r.get("error") else {**r, "note": f"Calling {c['name']}."}
    q = actions.queue_tool("phone_call", {"to": c["number"], "_confirmed": True}, f"Calling {c['name']}")
    q["auto"] = mode == "announce"
    return q


def notifications(count=8, app=""):
    return run("notifications", {"count": int(count or 8), "app": app or ""})


def reply_notification(nid, text, _confirmed=False):
    if _confirmed:
        return run("notif_reply", {"id": nid, "text": text})
    lst = run("notifications", {"count": 20})
    if lst.get("error"):
        return lst
    n = next((x for x in lst.get("notifications", []) if x["id"] == nid), None)
    if not n:
        return {"error": "That notification is gone. Read the notifications again."}
    if not n.get("can_reply"):
        return {"error": "That notification has no reply box."}
    q = actions.queue_tool("phone_reply_notification", {"nid": nid, "text": text, "_confirmed": True}, f"Reply to {n['from']} in {n['app']}: \"{text[:200]}\"")
    q["auto"] = _auto_text()
    return q


def screen_read():
    return run("screen_read")


def screen_tap(target, _confirmed=False):
    r = run("screen_tap", {"target": str(target), "force": bool(_confirmed)})
    if isinstance(r, dict) and r.get("needs_confirm") and not _confirmed:
        q = actions.queue_tool("phone_screen_tap", {"target": str(target), "_confirmed": True}, f"Tap '{r.get('label', '')}' in {r.get('app', 'the app')}")
        q["auto"] = False
        return q
    return r


def screen_type(text, target=""):
    return run("screen_type", {"text": text, "target": str(target or ""), "append": False})


def screen_scroll(direction="down"):
    return run("screen_scroll", {"direction": direction})


def screen_press(key):
    return run("screen_press", {"key": key})
