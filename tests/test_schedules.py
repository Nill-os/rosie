import datetime as dt

import pytest

import schedules

TZ = dt.timezone(dt.timedelta(hours=-5))
NOW = dt.datetime(2026, 10, 28, 12, 0, tzinfo=TZ)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(schedules, "DB", tmp_path / "schedules.db")


def at(day, h, m=0):
    return dt.datetime(2026, 10, day, h, m, tzinfo=TZ)


@pytest.mark.parametrize("text,spec", [
    ("daily at 8am", {"kind": "weekly", "days": list(range(7)), "time": "08:00"}),
    ("every day at 8:30pm", {"kind": "weekly", "days": list(range(7)), "time": "20:30"}),
    ("every monday 9:00", {"kind": "weekly", "days": [0], "time": "09:00"}),
    ("every mon and wed at 6pm", {"kind": "weekly", "days": [0, 2], "time": "18:00"}),
    ("weekdays 7:30", {"kind": "weekly", "days": [0, 1, 2, 3, 4], "time": "07:30"}),
    ("weekends at 10am", {"kind": "weekly", "days": [5, 6], "time": "10:00"}),
    ("12am daily", {"kind": "weekly", "days": list(range(7)), "time": "00:00"}),
])
def test_recurring_parse(text, spec):
    assert schedules.parse_when(text, NOW) == spec


def test_relative_and_once_parse():
    assert schedules.parse_when("in 2 hours", NOW)["at"] == at(28, 14).isoformat()
    assert schedules.parse_when("in 30 minutes", NOW)["at"] == at(28, 12, 30).isoformat()
    assert schedules.parse_when("in an hour", NOW)["at"] == at(28, 13).isoformat()
    assert schedules.parse_when("tomorrow at 9am", NOW) == {"kind": "once", "at": at(29, 9).isoformat()}
    assert schedules.parse_when("at 3pm", NOW)["at"] == at(28, 15).isoformat()
    assert schedules.parse_when("at 9am", NOW)["at"] == at(29, 9).isoformat()


@pytest.mark.parametrize("bad", ["", "whenever", "daily", "daily at 25:00", "in 0 hours", "today at 9am", "daily at 13pm"])
def test_bad_parse(bad):
    with pytest.raises(ValueError):
        schedules.parse_when(bad, NOW)


def test_next_run():
    spec = schedules.parse_when("weekdays 7:30", NOW)
    assert schedules.next_run(spec, NOW) == at(29, 7, 30)
    assert schedules.next_run(spec, at(30, 8)) == dt.datetime(2026, 11, 2, 7, 30, tzinfo=TZ)
    assert schedules.next_run({"kind": "once", "at": at(27, 8).isoformat()}, NOW) is None


def test_add_list_pause_remove_and_owner_isolation():
    r = schedules.add("Summarize my inbox", "daily at 8am", now=NOW)
    assert r["status"] == "scheduled" and r["schedule"] == "daily at 08:00"
    assert schedules.add("", "daily at 8am", now=NOW)["error"]
    assert schedules.add("x", "never", now=NOW)["error"]
    sid = r["id"]
    assert schedules.pause(sid, "other")["error"] and schedules.remove(sid, "other")["error"]
    assert schedules.list_("other") == []
    assert schedules.pause(sid, now=NOW)["status"] == "paused"
    assert schedules.list_()[0]["status"] == "paused"
    assert schedules.due(at(30, 9)) == []
    assert schedules.pause(sid, paused=False, now=at(30, 9))["status"] == "resumed"
    assert schedules.remove(sid)["status"] == "removed" and schedules.list_() == []


def test_cap_per_owner():
    for i in range(schedules.MAX_ACTIVE):
        assert schedules.add(f"p{i}", "daily at 8am", now=NOW)["status"] == "scheduled"
    assert "already have" in schedules.add("more", "daily at 8am", now=NOW)["error"]
    assert schedules.add("mine", "daily at 8am", owner="b", now=NOW)["status"] == "scheduled"


def test_missed_runs_coalesce_to_one():
    schedules.add("Daily digest", "daily at 8am", now=NOW)
    seen = []
    done = schedules.run_due(at(31, 10), runner=lambda p: seen.append(p) or "ok")
    assert len(done) == 1 and len(seen) == 1
    assert "missed 2 earlier runs" in seen[0] and "Daily digest" in seen[0]
    assert schedules.run_due(at(31, 11), runner=lambda p: seen.append(p)) == []
    assert schedules.list_()[0]["next_run"].startswith("2026-11-01T08:00")
    log = schedules.runs()
    assert len(log) == 1 and log[0]["status"] == "ok" and log[0]["missed"] == 2


def test_once_runs_then_done_and_stale_once_expires():
    schedules.add("ping", "in 1 hour", now=NOW)
    schedules.add("late", "in 2 hours", now=NOW)
    seen = []
    schedules.run_due(at(28, 13, 5), runner=lambda p: seen.append(p) or "ok")
    assert len(seen) == 1 and "ping" in seen[0]
    assert schedules.run_due(at(28, 13, 6), runner=lambda p: seen.append(p)) == []
    assert schedules.run_due(at(30, 9), runner=lambda p: seen.append(p)) == []
    assert len(seen) == 1
    st = {s["prompt"]: s["status"] for s in schedules.list_(include_done=True)}
    assert st == {"ping": "done", "late": "expired"}
    assert schedules.list_() == []


def test_stale_pause_expires():
    sid = schedules.add("x", "daily at 8am", now=NOW)["id"]
    schedules.pause(sid, now=NOW)
    schedules.due(NOW + dt.timedelta(days=schedules.STALE_PAUSED_DAYS + 1))
    assert schedules.list_(include_done=True)[0]["status"] == "expired"
    assert schedules.pause(sid)["error"]


def test_repeated_failures_disable_and_log():
    sid = schedules.add("boom", "daily at 8am", now=NOW)["id"]

    def bad(p):
        raise RuntimeError("down")
    for d in range(29, 29 + schedules.MAX_FAILS):
        schedules.run_due(at(d, 9) if d < 31 else dt.datetime(2026, 11, d - 30, 9, tzinfo=TZ), runner=bad)
    assert schedules.list_(include_done=True)[0]["status"] == "expired"
    assert all(r["status"] == "error" for r in schedules.runs(sid))
    assert "RuntimeError" in schedules.runs(sid)[0]["note"]


def test_notify_gets_reply_and_tool_api():
    schedules.add("hi", "in 10 minutes", now=NOW)
    got = []
    schedules.run_due(at(28, 12, 11), runner=lambda p: "reply", notify=lambda item, ok, text: got.append((item["prompt"], ok, text)))
    assert got == [("hi", True, "reply")]
    assert schedules.tool("add", "again", "daily at 7am")["status"] == "scheduled"
    assert schedules.tool("list")["schedules"][0]["prompt"] == "again"
    assert schedules.tool("nope")["error"]
    assert schedules.api_list()["max"] == schedules.MAX_ACTIVE
