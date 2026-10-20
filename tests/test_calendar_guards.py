import datetime as dt

import pytest

import actions
import gcal

TZ = gcal.TZ


class Cal:
    def __init__(self):
        self.events = []
        self.n = 0

    def add(self, title, start, end):
        self.n += 1
        e = {"id": f"e{self.n}", "title": title, "start": start.isoformat(), "end": end.isoformat(), "all_day": False, "location": ""}
        self.events.append(e)
        return e


@pytest.fixture
def cal(monkeypatch):
    c = Cal()
    d = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    c.add("Existing meeting", d.replace(hour=10), d.replace(hour=11))
    c.day = d

    def overlaps(s, e, calendar_id="primary", query=None):
        return [x for x in c.events if dt.datetime.fromisoformat(x["start"]) < e and dt.datetime.fromisoformat(x["end"]) > s]
    monkeypatch.setattr(gcal, "list_events", overlaps)
    monkeypatch.setattr(gcal, "events_on", lambda day, calendar_id="primary": overlaps(dt.datetime.combine(day, dt.time.min, tzinfo=TZ), dt.datetime.combine(day, dt.time.max, tzinfo=TZ)))
    monkeypatch.setattr(gcal, "write_calendar_id", lambda: "test")
    monkeypatch.setattr(gcal, "add_event", lambda title, s, e, calendar_id=None, location="": c.add(title, s, e))
    monkeypatch.setattr(gcal, "get_event", lambda eid, cid=None: next(x for x in c.events if x["id"] == eid))

    def move(eid, s, e, calendar_id=None):
        ev = next(x for x in c.events if x["id"] == eid)
        ev["start"], ev["end"] = s.isoformat(), e.isoformat()
        return ev
    monkeypatch.setattr(gcal, "move_event", move)
    return c


def at(c, h):
    return c.day.replace(hour=h).isoformat()


def test_clash_is_refused_with_free_slots(cal):
    r = actions.add_event("Dentist", at(cal, 10), at(cal, 11))
    assert r["status"] == "conflict" and r["conflicts"][0]["title"] == "Existing meeting" and r["free_slots"]
    assert "Do NOT ask" in r["message"] and len(cal.events) == 1


def test_free_time_is_added(cal):
    assert actions.add_event("Dentist", at(cal, 13), at(cal, 14))["status"] == "done"
    assert len(cal.events) == 2


def test_the_same_event_is_not_added_twice(cal):
    actions.add_event("Dentist", at(cal, 13), at(cal, 14))
    r = actions.add_event("dentist", at(cal, 13), at(cal, 14))
    assert r["status"] == "done" and "already" in r["note"] and len(cal.events) == 2


def test_move_by_title_and_clash_on_move(cal):
    actions.add_event("Dentist", at(cal, 13), at(cal, 14))
    r = actions.move_event("dentist", at(cal, 10), at(cal, 11))
    assert r["status"] == "conflict" and "NOT moved" in r["message"]
    r = actions.move_event("dentist", at(cal, 15), at(cal, 16))
    assert r["status"] == "done" and r["event"]["start"].startswith(cal.day.replace(hour=15).isoformat()[:13])


def test_made_up_id_is_an_error_not_done(cal):
    r = actions.move_event("09:00 Sun 9:00 Yard work", at(cal, 15), at(cal, 16))
    assert r["status"] == "error" and "could not find" in r["message"].lower()


def test_double_booking_needs_the_confirm_card(cal):
    r = actions.add_event("Dentist", at(cal, 10), at(cal, 11), double_book=True)
    assert r["status"] == "needs_confirmation" and "overlaps" in r["summary"]
