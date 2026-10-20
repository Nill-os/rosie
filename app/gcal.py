"""Google Calendar access. Token lives in secrets/, never in code."""
import datetime as dt
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from tzlocal import get_localzone

ROOT = Path(__file__).resolve().parent.parent
CLIENT_FILE = ROOT / "secrets" / "google_client.json"
TOKEN_FILE = ROOT / "secrets" / "google_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TZ = get_localzone()


class LoginExpired(Exception):
    """Google login is missing or expired; run ./login.sh"""


def login():
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    try:
        import health
        health.record_login("google")
    except Exception:
        pass


def service():
    if not TOKEN_FILE.exists():
        raise LoginExpired("Not logged in to Google. Run ./login.sh")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise LoginExpired("Google login expired (happens every 7 days). Run ./login.sh") from e
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _fmt(ev):
    s, e = ev["start"], ev["end"]
    all_day = "date" in s
    return {
        "id": ev["id"],
        "title": ev.get("summary", "(no title)"),
        "start": s.get("dateTime") or s.get("date"),
        "end": e.get("dateTime") or e.get("date"),
        "all_day": all_day,
        "location": ev.get("location", ""),
    }


def list_events(start: dt.datetime, end: dt.datetime, calendar_id="primary", query=None):
    items, token = [], None
    for _ in range(10):                                            # follow the pages: a busy calendar must not silently lose events
        r = service().events().list(
            calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", q=query, maxResults=250, pageToken=token,
        ).execute()
        items += r.get("items", [])
        token = r.get("nextPageToken")
        if not token:
            break
    return [_fmt(e) for e in items if e.get("status") != "cancelled"]


def events_on(day: dt.date, calendar_id="primary"):
    start = dt.datetime.combine(day, dt.time.min, tzinfo=TZ)
    return list_events(start, start + dt.timedelta(days=1), calendar_id)


def search(query: str, days_ahead=60, calendar_id="primary"):
    start = dt.datetime.combine(dt.date.today(), dt.time.min, tzinfo=TZ)  # include earlier today
    return list_events(start, start + dt.timedelta(days=days_ahead), calendar_id, query)


# ---------- writes ----------
def _service_cal():
    return service().events()


def get_or_create_test_calendar():
    from config import load, save
    cfg = load()
    if cfg["test_calendar_id"]:
        return cfg["test_calendar_id"]
    cal = service().calendars().insert(body={"summary": "Assistant Test", "timeZone": str(TZ)}).execute()
    cfg["test_calendar_id"] = cal["id"]
    save(cfg)
    return cal["id"]


def write_calendar_id():
    from config import load
    cfg = load()
    return "primary" if cfg["write_target"] == "primary" else get_or_create_test_calendar()


def add_event(title, start: dt.datetime, end: dt.datetime, calendar_id=None, location=""):
    body = {"summary": title, "location": location,
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
    ev = _service_cal().insert(calendarId=calendar_id or write_calendar_id(), body=body).execute()
    return _fmt(ev)


def move_event(event_id, start: dt.datetime, end: dt.datetime, calendar_id=None):
    body = {"start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}}
    ev = _service_cal().patch(calendarId=calendar_id or write_calendar_id(), eventId=event_id, body=body).execute()
    return _fmt(ev)


def delete_event(event_id, calendar_id=None):
    _service_cal().delete(calendarId=calendar_id or write_calendar_id(), eventId=event_id).execute()


# ---------- conflicts and free time ----------
def get_event(event_id, calendar_id=None):
    return _fmt(_service_cal().get(calendarId=calendar_id or write_calendar_id(), eventId=event_id).execute())


def conflicts(start: dt.datetime, end: dt.datetime, exclude_id=None):
    """Timed events (on the main calendar) that overlap [start, end). All-day events are not counted as clashes."""
    out = []
    for e in list_events(start, end, "primary"):
        if e["all_day"] or e["id"] == exclude_id:
            continue
        s2, e2 = dt.datetime.fromisoformat(e["start"]), dt.datetime.fromisoformat(e["end"])
        if s2 < end and e2 > start:
            out.append({"title": e["title"], "start": e["start"], "end": e["end"]})
    return out


def free_slots(day: dt.date, minutes=60, earliest="08:00", latest="21:00", exclude_id=None):
    """Open gaps on a day that are at least `minutes` long, between earliest and latest (never in the past)."""
    h1, m1 = map(int, earliest.split(":"))
    h2, m2 = map(int, latest.split(":"))
    lo = dt.datetime.combine(day, dt.time(h1, m1), tzinfo=TZ)
    hi = dt.datetime.combine(day, dt.time(h2, m2), tzinfo=TZ)
    now = dt.datetime.now(TZ)
    if day == now.date():
        lo = max(lo, now.replace(second=0, microsecond=0) + dt.timedelta(minutes=15 - now.minute % 15))
    busy = sorted((dt.datetime.fromisoformat(e["start"]), dt.datetime.fromisoformat(e["end"])) for e in events_on(day) if not e["all_day"] and e["id"] != exclude_id)
    slots, cur = [], lo
    for s0, e0 in busy:
        if s0 - cur >= dt.timedelta(minutes=minutes):
            slots.append((cur, min(s0, hi)))
        cur = max(cur, e0)
    if hi - cur >= dt.timedelta(minutes=minutes):
        slots.append((cur, hi))
    return [{"date": day.isoformat(), "from": a.strftime("%H:%M"), "to": b.strftime("%H:%M")} for a, b in slots if b > a]
