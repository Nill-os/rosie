# board #716 owner-canvas-into-rosie
import datetime as dt
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parent.parent
SECRET = ROOT / "secrets" / "canvas.json"
EXTRA = ROOT / "data" / "canvas_extra.json"
STATE = ROOT / "data" / "canvas_state.json"
MAX_FEED = 5_000_000  # board #716 why-feed-size-cap
REMIND_MIN = 15 * 60  # board #716 why-popup-nine-am
_lock = threading.Lock()


def _read(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def _write(p: Path, data, private=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    if private:
        tmp.chmod(0o600)
    tmp.replace(p)


def feed_url() -> str:
    return str(_read(SECRET, {}).get("ics_url") or "")


def set_url(url: str) -> dict:
    # board #716 safety-https-only-feed
    url = (url or "").strip()
    if url:
        u = urlsplit(url)
        if u.scheme != "https" or not u.netloc or len(url) > 600 or any(c.isspace() for c in url):
            raise ValueError("Paste the https link from Canvas: Calendar > Calendar Feed.")
    _write(SECRET, {"ics_url": url}, private=True)
    return status()


def status() -> dict:
    url = feed_url()
    st = _read(STATE, {})
    return {"on": bool(url), "feed": (urlsplit(url).netloc + "/…") if url else "", "last_run": st.get("last_run", ""),
            "added": st.get("added", 0), "updated": st.get("updated", 0), "upcoming": st.get("upcoming", 0), "error": st.get("error", "")}


def _unescape(v: str) -> str:
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def parse(ics: str, tz=None) -> list[dict]:
    tz = tz or dt.datetime.now().astimezone().tzinfo
    raw = re.sub(r"\r?\n[ \t]", "", ics)
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        f = {}
        for line in block.strip().splitlines():
            k, _, v = line.partition(":")
            f.setdefault(k.split(";")[0].upper(), v)
        v = f.get("DTSTART", "").strip()
        if not re.fullmatch(r"\d{8}(T\d{6}Z?)?", v):
            continue
        if "T" not in v:
            day = dt.date(int(v[:4]), int(v[4:6]), int(v[6:8]))
        else:
            t = dt.datetime.strptime(v.rstrip("Z"), "%Y%m%dT%H%M%S")
            day = (t.replace(tzinfo=dt.timezone.utc).astimezone(tz) if v.endswith("Z") else t).date()
        title = _unescape(f.get("SUMMARY", ""))
        m = re.search(r"\[([^\]]+)\]\s*$", title)
        uid = _unescape(f.get("UID", "")) or f"{day}:{title}"
        out.append({"uid": uid[:200], "title": re.sub(r"\s*\[[^\]]+\]\s*$", "", title), "course": m.group(1) if m else "",
                    "start": day.isoformat(), "url": _unescape(f.get("URL", ""))})
    return out


def fetch(url: str) -> str:
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        if len(r.content) > MAX_FEED:
            raise ValueError("The Canvas feed is unexpectedly large.")
        return r.text


def event_body(e: dict) -> dict:
    day = dt.date.fromisoformat(e["start"])
    course = (e.get("course") or "").split(" ")[0] or "School"
    return {
        "summary": f"{course}: {' '.join(e['title'].split())}"[:150],
        "start": {"date": day.isoformat()}, "end": {"date": (day + dt.timedelta(days=1)).isoformat()},
        "description": "Due by 11:59 PM." + (f"\nCanvas: {e['url']}" if e.get("url") else "\nFrom the course syllabus.") + "\nAdded from Canvas by Rosie.",
        "colorId": "9", "transparency": "transparent",
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": REMIND_MIN}]},
        "extendedProperties": {"private": {"school_uid": e["uid"]}},
    }


def sync(svc=None, calendar_id=None, today=None, ics=None) -> dict:
    import gcal
    with _lock:
        url = feed_url()
        if not url and ics is None:
            return {"error": "No Canvas feed link yet: paste it in Settings > Canvas."}
        today = today or dt.date.today()
        try:
            items = parse(ics if ics is not None else fetch(url)) + [x for x in _read(EXTRA, []) if isinstance(x, dict) and x.get("uid") and x.get("start")]
            svc = svc or gcal.service()
            cal = calendar_id or gcal.write_calendar_id()
            added = updated = upcoming = 0
            for e in items:
                if dt.date.fromisoformat(e["start"]) < today:
                    continue
                upcoming += 1
                body = event_body(e)
                found = svc.events().list(calendarId=cal, privateExtendedProperty=f"school_uid={e['uid']}", maxResults=1).execute().get("items", [])
                if found:
                    old = found[0]
                    if old.get("summary") != body["summary"] or old.get("start", {}).get("date") != body["start"]["date"]:
                        svc.events().patch(calendarId=cal, eventId=old["id"], body=body).execute()
                        updated += 1
                else:
                    svc.events().insert(calendarId=cal, body=body).execute()
                    added += 1
            res = {"last_run": dt.datetime.now().isoformat(timespec="minutes"), "day": today.isoformat(), "added": added, "updated": updated,
                   "upcoming": upcoming, "error": ""}
        except Exception as ex:
            res = {**_read(STATE, {}), "day": today.isoformat(), "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
        _write(STATE, res)
        return res


def due_today_run(now: dt.datetime, at: str = "07:00") -> bool:
    if not feed_url():
        return False
    h, m = (int(x) for x in (at or "07:00").split(":"))
    return (now.hour, now.minute) >= (h, m) and _read(STATE, {}).get("day") != now.date().isoformat()
