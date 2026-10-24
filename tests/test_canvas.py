"""Canvas due dates in the calendar (owner 2026-10-24: the partner's daily Canvas task moved into Rosie). Google and Canvas are fakes:
nothing here can touch a real calendar or feed."""
import datetime as dt
import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient

import automations
import canvas
import gcal

KEY = "testkey-0123456789abcdef0123456789abcdef"
FEED = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:event-assignment-101\r
DTSTART:20261026T065900Z\r
SUMMARY:Mid Term exam [ENVS&101 Environmental Science]\r
URL:https://school.instructure.com/courses/1/assignments/101\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:event-assignment-102\r
DTSTART;VALUE=DATE:20261030\r
SUMMARY:Annotated Bibliography\\, draft [ENGL&235 Technical\r
  Writing]\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:event-old\r
DTSTART:20261001T120000Z\r
SUMMARY:Old quiz [ENVS&101 Environmental Science]\r
END:VEVENT\r
END:VCALENDAR\r
"""


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeCal:
    """events().list / insert / patch over a dict, keyed by the private school_uid."""
    def __init__(self):
        self.items, self.calls = {}, []

    def events(self):
        return self

    def list(self, calendarId, privateExtendedProperty, maxResults):
        uid = privateExtendedProperty.split("=", 1)[1]
        return Req(lambda: {"items": [self.items[uid]] if uid in self.items else []})

    def insert(self, calendarId, body):
        def run():
            self.calls.append(("insert", calendarId, body["summary"]))
            self.items[body["extendedProperties"]["private"]["school_uid"]] = {**body, "id": f"e{len(self.items)}"}
        return Req(run)

    def patch(self, calendarId, eventId, body):
        def run():
            self.calls.append(("patch", calendarId, body["summary"]))
            self.items[body["extendedProperties"]["private"]["school_uid"]].update(body)
        return Req(run)


def test_the_feed_parses_to_local_dates_courses_and_unescaped_titles():
    items = canvas.parse(FEED, tz=dt.timezone(dt.timedelta(hours=-7)))
    first = items[0]
    assert first["start"] == "2026-10-25" and first["course"] == "ENVS&101 Environmental Science" and first["title"] == "Mid Term exam"   # 06:59 UTC is the evening before
    second = items[1]
    assert second["start"] == "2026-10-30" and second["title"] == "Annotated Bibliography, draft" and second["course"].startswith("ENGL&235")


def test_a_sync_adds_upcoming_dates_once_and_updates_a_changed_one(tmp_path):
    cal = FakeCal()
    r = canvas.sync(svc=cal, calendar_id="primary", today=dt.date(2026, 10, 20), ics=FEED)
    assert (r["added"], r["updated"], r["upcoming"]) == (2, 0, 2) and not r["error"]
    body = cal.items["event-assignment-101"]
    assert body["summary"] == "ENVS&101: Mid Term exam" and body["start"]["date"] and body["reminders"]["overrides"][0]["minutes"] == 15 * 60
    assert "event-old" not in cal.items                                    # the past is left alone
    again = canvas.sync(svc=cal, calendar_id="primary", today=dt.date(2026, 10, 20), ics=FEED)
    assert (again["added"], again["updated"]) == (0, 0)                     # a rerun adds no copies
    moved = FEED.replace("DTSTART;VALUE=DATE:20261030", "DTSTART;VALUE=DATE:20261031")
    assert canvas.sync(svc=cal, calendar_id="primary", today=dt.date(2026, 10, 20), ics=moved)["updated"] == 1


def test_extra_syllabus_dates_join_the_sync(tmp_path):
    canvas.EXTRA.parent.mkdir(parents=True, exist_ok=True)
    canvas.EXTRA.write_text(json.dumps([{"uid": "it461-finals", "title": "Finals week starts", "course": "IT461", "start": "2026-12-07"}]))
    cal = FakeCal()
    canvas.sync(svc=cal, calendar_id="primary", today=dt.date(2026, 10, 20), ics=FEED)
    assert cal.items["it461-finals"]["summary"] == "IT461: Finals week starts" and "syllabus" in cal.items["it461-finals"]["description"]


def test_the_link_must_be_https_and_is_never_shown_in_full():
    with pytest.raises(ValueError):
        canvas.set_url("http://school.instructure.com/feeds/calendars/user_secret.ics")
    st = canvas.set_url("https://school.instructure.com/feeds/calendars/user_SECRET123.ics")
    assert st["on"] and st["feed"] == "school.instructure.com/…" and "SECRET" not in json.dumps(st)
    if os.name != "nt":                                                 # Windows has no owner-only mode bits (always 0o666)
        assert oct(canvas.SECRET.stat().st_mode & 0o777) == "0o600"
    assert not canvas.set_url("")["on"]


def test_a_failure_is_kept_for_the_page_and_the_daily_run_happens_once(monkeypatch):
    canvas.set_url("https://school.instructure.com/feeds/calendars/user_x.ics")
    monkeypatch.setattr(canvas, "fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))
    r = canvas.sync(svc=FakeCal(), calendar_id="primary", today=dt.date(2026, 10, 24))
    assert "offline" in r["error"] and canvas.status()["error"]
    morning = dt.datetime(2026, 10, 24, 6, 30)
    assert not canvas.due_today_run(morning, "07:00")                       # before the time
    assert not canvas.due_today_run(dt.datetime(2026, 10, 24, 8, 0), "07:00")    # today's run (even a failed one) already happened
    assert canvas.due_today_run(dt.datetime(2026, 10, 25, 8, 0), "07:00")
    assert "job_canvas" in [j.__name__ for j in automations.JOBS]


def test_the_api_saves_the_link_and_syncs(monkeypatch, tmp_path):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text(KEY)
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(KEY.encode(), b"assistant-ok", hashlib.sha256).hexdigest())
    assert c.get("/api/canvas").json()["on"] is False
    assert c.post("/api/canvas/url", json={"url": "ftp://x"}).status_code == 400
    assert c.post("/api/canvas/url", json={"url": "https://school.instructure.com/feeds/calendars/u.ics"}).json()["on"]
    cal = FakeCal()
    monkeypatch.setattr(canvas, "fetch", lambda url: FEED)
    monkeypatch.setattr(gcal, "service", lambda: cal)
    monkeypatch.setattr(gcal, "write_calendar_id", lambda: "test-cal")
    monkeypatch.setattr(canvas.dt, "date", type("D", (dt.date,), {"today": classmethod(lambda cls: dt.date(2026, 10, 20))}))
    r = c.post("/api/canvas/sync").json()
    assert r["result"]["added"] == 2 and all(call[1] == "test-cal" for call in cal.calls)   # it writes where Rosie's calendar writes go
