import hashlib
import hmac
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import jobs  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "_marks", {})
    monkeypatch.setattr(jobs, "_units_cache", {"at": 0.0, "data": {}})
    monkeypatch.setattr(jobs, "BOOT", time.time() - 10 * 86400)
    monkeypatch.setattr(jobs.config, "load", lambda: {"model_scout": True, "security_watch": True, "token_report_on": True, "brief_on": False, "mail_on": False})
    monkeypatch.setattr(jobs.shutil, "which", lambda n: None)
    import paper
    monkeypatch.setattr(paper, "settings", lambda cfg=None: {"on": False})


def age(name, secs):
    import json
    jobs._marks[name]["at"] = time.time() - secs
    jobs.FILE.write_text(json.dumps(jobs._marks))


def by_name(rows):
    return {r["name"]: r for r in rows}


def test_enabled_job_that_never_ran_is_late_after_boot_window():
    assert by_name(jobs.listing())["tokens"]["state"] == "late"


def test_fresh_mark_is_ok_and_old_mark_is_late():
    jobs.mark("tokens", True)
    assert by_name(jobs.listing())["tokens"]["state"] == "ok"
    age("tokens", 3 * 3600)
    r = by_name(jobs.listing())["tokens"]
    assert r["state"] == "late" and r["next"]


def test_failure_is_bad_with_the_error():
    jobs.mark("tokens", False, "boom")
    r = by_name(jobs.listing())["tokens"]
    assert r["state"] == "bad" and "boom" in r["result"]


def test_disabled_job_is_off_not_bad():
    jobs.mark("brief", False, "boom")
    r = by_name(jobs.listing())
    assert r["brief"]["state"] == "off" and r["paper"]["state"] == "off"


def test_sorted_bad_late_ok_off():
    jobs.mark("tokens", False, "x")
    jobs.mark("security_watch", True)
    jobs._marks["model_scout"] = {"at": time.time() - 90 * 3600, "ok": True, "err": ""}
    order = [r["state"] for r in jobs.listing()]
    assert order == sorted(order, key=jobs.ORDER.get)
    assert order[0] == "bad" and "late" in order and "ok" in order and order[-1] == "off"


def test_mark_persists(tmp_path):
    jobs.mark("tokens", True)
    assert "tokens" in (tmp_path / "jobs.json").read_text()


def test_windows_units_say_linux_only(monkeypatch):
    monkeypatch.setattr(jobs.plat, "IS_WIN", True)
    r = by_name(jobs.listing())
    assert r["rosie-backup"]["state"] == "off" and r["rosie-backup"]["result"] == "off (Linux only)"
    assert r["assistant-dictation"]["result"] == "off (Linux only)"
    for spec in jobs.UNITS:
        assert r[spec[0]]["state"] == "off" and r[spec[0]]["result"] == "off (Linux only)", spec[0]


def test_unit_rows_from_systemd(monkeypatch):
    now = time.time()
    data = {
        "rosie-backup.timer": {"Id": "rosie-backup.timer", "ActiveState": "active", "LastTriggerUSec": f"@{now - 100}"},
        "rosie-backup.service": {"Id": "rosie-backup.service", "Result": "exit-code"},
        "rosie-sync.timer": {"Id": "rosie-sync.timer", "ActiveState": "active", "LastTriggerUSec": f"@{now - 5 * 86400}"},
        "rosie-sync.service": {"Id": "rosie-sync.service", "Result": "success"},
        "assistant-dictation.service": {"Id": "assistant-dictation.service", "ActiveState": "inactive", "UnitFileState": "enabled", "Result": "success"},
        "assistant-overlay.service": {"Id": "assistant-overlay.service", "ActiveState": "inactive", "UnitFileState": "disabled", "Result": "success"},
    }
    monkeypatch.setattr(jobs.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(jobs.plat, "IS_WIN", False)
    monkeypatch.setattr(jobs, "_units", lambda: data)
    r = by_name(jobs.listing())
    assert r["rosie-backup"]["state"] == "bad"
    assert r["rosie-sync"]["state"] == "late"
    assert r["assistant-dictation"]["state"] == "bad"
    assert r["assistant-overlay"]["state"] == "off"
    assert r["graphiti-mcp"]["result"] == "not installed"


def test_parse_ts_formats():
    assert jobs._parse_ts("@12.5") == 12.5
    assert jobs._parse_ts("") is None
    assert jobs._parse_ts("Thu 2026-11-05 07:47:29 PST") > 0


def test_api_needs_login_and_has_shape(tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    assert c.get("/api/jobs").status_code == 401
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    r = c.get("/api/jobs").json()
    assert {"rows", "bad", "late", "level"} <= set(r)
    assert {"name", "what", "state", "last", "result", "next"} <= set(r["rows"][0])


def test_registry_is_wired_into_automations():
    import automations
    src = Path(automations.__file__).read_text()
    assert 'jobs.mark("scheduler"' in src and 'jobs.mark("paper"' in src


def _stub(monkeypatch, accounts=(), feed="", brief_created=None):
    import briefs
    import canvas
    import gmail
    monkeypatch.setattr(gmail, "accounts", lambda: list(accounts))
    monkeypatch.setattr(canvas, "feed_url", lambda: feed)
    monkeypatch.setattr(briefs, "recent", lambda n=1: [{"created": brief_created}] if brief_created else [])


def test_mail_and_canvas_are_off_without_an_account_or_feed(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(jobs.config, "load", lambda: {"mail_on": True, "brief_on": False})
    r = by_name(jobs.listing())
    assert r["mail"]["state"] == "off" and r["canvas"]["state"] == "off"


def test_mail_and_canvas_can_go_late_once_set_up(monkeypatch):
    _stub(monkeypatch, accounts=[{"name": "main"}], feed="http://x")
    monkeypatch.setattr(jobs.config, "load", lambda: {"mail_on": True, "mail_check_min": 10, "brief_on": False})
    r = by_name(jobs.listing())
    assert r["mail"]["state"] == "late" and r["canvas"]["state"] == "late"


def test_mail_interval_follows_the_setting(monkeypatch):
    _stub(monkeypatch, accounts=[{"name": "main"}])
    monkeypatch.setattr(jobs.config, "load", lambda: {"mail_on": True, "mail_check_min": 120, "brief_on": False})
    jobs.mark("mail", True)
    age("mail", 3 * 3600)
    assert by_name(jobs.listing())["mail"]["state"] == "ok"


def test_a_brief_made_from_the_ui_counts_as_a_run(monkeypatch):
    import datetime as dt
    _stub(monkeypatch, brief_created=dt.datetime.now().isoformat(timespec="seconds"))
    monkeypatch.setattr(jobs.config, "load", lambda: {"brief_on": True})
    r = by_name(jobs.listing())["brief"]
    assert r["state"] == "ok" and r["last"]


def test_a_failing_step_outside_the_registry_shows_then_expires(monkeypatch):
    _stub(monkeypatch)
    jobs.mark("stray_job", False, "boom")
    jobs.mark("stray_ok", True)
    r = by_name(jobs.listing())
    assert r["stray_job"]["state"] == "bad" and "stray_ok" not in r
    age("stray_job", 7 * 3600)
    assert "stray_job" not in by_name(jobs.listing())


def test_systemctl_failure_is_cached_and_shows_one_row(monkeypatch):
    calls = []

    def boom():
        calls.append(1)
        raise OSError("no bus")

    monkeypatch.setattr(jobs, "_read_units", boom)
    monkeypatch.setattr(jobs.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(jobs.plat, "IS_WIN", False)
    _stub(monkeypatch)
    rows = jobs.listing()
    jobs.listing()
    assert len(calls) == 1
    assert by_name(rows)["systemd"]["result"] == "not reachable"
    assert not any(r["result"] == "not installed" for r in rows)


def test_marks_from_another_process_show_and_are_kept_on_write(tmp_path, monkeypatch):
    import json
    _stub(monkeypatch)
    (tmp_path / "jobs.json").write_text(json.dumps({"tokens": {"at": time.time(), "ok": False, "err": "other proc"}}))
    assert by_name(jobs.listing())["tokens"]["state"] == "bad"
    (tmp_path / "jobs.json").write_text(json.dumps({"tokens": {"at": time.time(), "ok": False, "err": "other proc"}, "health": {"at": time.time(), "ok": True, "err": ""}}))
    jobs.mark("security_watch", True)
    saved = json.loads((tmp_path / "jobs.json").read_text())
    assert {"tokens", "health", "security_watch"} <= set(saved)


def test_tick_jobs_have_rows_and_failures_show(monkeypatch):
    import automations
    import gcal
    monkeypatch.setattr(automations.config, "load", lambda: {"remind_events": True})
    monkeypatch.setattr(automations, "JOBS", [])

    def job_events(now, cfg):
        raise RuntimeError("ServerNotFoundError")

    def job_reminders(now, cfg):
        return None

    def job_meeting(now, cfg):
        raise gcal.LoginExpired()

    monkeypatch.setattr(automations, "JOBS", [job_events, job_reminders, job_meeting])
    monkeypatch.setattr(automations, "_login_alert", lambda now: None)
    monkeypatch.setattr(automations, "job_corner", lambda now, cfg: None)
    automations.tick()
    r = by_name(jobs.listing())
    assert r["events"]["state"] == "bad" and "ServerNotFoundError" in r["events"]["result"]
    assert r["reminders"]["state"] == "ok"
    assert r["calendar"]["state"] == "bad" and "log in again" in r["calendar"]["result"].lower()
    assert r["meeting"]["state"] == "bad" and "login expired" in r["meeting"]["result"].lower()


def test_unknown_failing_job_still_shows_stray():
    jobs.mark("zzz_unknown", False, "boom")
    assert by_name(jobs.listing())["zzz_unknown"]["state"] == "bad"


def test_mark_ok_many_writes_once_per_heartbeat(monkeypatch):
    saves = []
    monkeypatch.setattr(jobs, "_save", lambda: saves.append(1))
    jobs.mark_ok_many(["events", "reminders"])
    jobs.mark_ok_many(["events", "reminders"])
    assert len(saves) == 1


def test_timer_that_never_fired_goes_late(monkeypatch):
    now = time.time()
    data = {
        "demo-late.timer": {"Id": "demo-late.timer", "ActiveState": "active", "ActiveEnterTimestamp": f"@{now - 3 * 86400}"},
        "demo-late.service": {"Id": "demo-late.service", "Result": "success"},
        "demo-fresh.timer": {"Id": "demo-fresh.timer", "ActiveState": "active", "ActiveEnterTimestamp": f"@{now - 3600}"},
        "demo-fresh.service": {"Id": "demo-fresh.service", "Result": "success"},
    }
    monkeypatch.setattr(jobs, "UNITS", [("demo-late", "timer", "Late demo", jobs.D), ("demo-fresh", "timer", "Fresh demo", jobs.D)])
    monkeypatch.setattr(jobs.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(jobs.plat, "IS_WIN", False)
    monkeypatch.setattr(jobs, "_units", lambda: data)
    r = by_name(jobs.listing())
    assert r["demo-late"]["state"] == "late"
    assert r["demo-fresh"]["state"] == "ok"


def test_calendar_row_is_off_when_event_reminders_are_off(monkeypatch):
    import automations
    monkeypatch.setattr(automations.config, "load", lambda: {"remind_events": False})
    monkeypatch.setattr(automations, "JOBS", [])
    monkeypatch.setattr(automations, "job_corner", lambda now, cfg: None)
    automations.tick()
    assert by_name(jobs.listing())["calendar"]["state"] == "off"
    monkeypatch.setattr(automations.config, "load", lambda: {"remind_events": True})
    automations.tick()
    assert by_name(jobs.listing())["calendar"]["state"] == "ok"


def test_login_alert_failure_does_not_skip_the_job_marks(monkeypatch):
    import automations
    import gcal
    monkeypatch.setattr(automations.config, "load", lambda: {"remind_events": True})

    def job_reminders(now, cfg):
        return None

    def job_meeting(now, cfg):
        raise gcal.LoginExpired()

    def boom(now):
        raise OSError("inbox locked")

    monkeypatch.setattr(automations, "JOBS", [job_meeting, job_reminders])
    monkeypatch.setattr(automations, "_login_alert", boom)
    monkeypatch.setattr(automations, "job_corner", lambda now, cfg: None)
    automations.tick()
    r = by_name(jobs.listing())
    assert r["reminders"]["state"] == "ok"
    assert r["calendar"]["state"] == "bad"
    assert r["scheduler"]["state"] == "ok"


def test_paper_is_not_marked_ok_when_nothing_was_built(monkeypatch):
    import automations
    import paper
    import datetime as dt
    monkeypatch.setattr(paper, "fake_loaded", lambda: False)
    monkeypatch.setattr(paper, "morning_due", lambda now: True)
    monkeypatch.setattr(paper, "claim_run", lambda day, now: True)
    monkeypatch.setattr(paper, "state", {"running": False})
    monkeypatch.setattr(automations.threading, "Thread", lambda target, **kw: type("T", (), {"start": staticmethod(target)})())
    monkeypatch.setattr(paper, "morning", lambda now=None: {"running": True})
    automations.job_paper(dt.datetime.now(), {})
    assert "paper" not in jobs._marks
    monkeypatch.setattr(paper, "morning", lambda now=None: {"day": "x"})
    monkeypatch.setattr(paper, "settings", lambda cfg=None: {"on": False})
    automations.job_paper(dt.datetime.now(), {})
    assert "paper" not in jobs._marks
    monkeypatch.setattr(paper, "settings", lambda cfg=None: {"on": True})
    automations.job_paper(dt.datetime.now(), {})
    assert jobs._marks["paper"]["ok"] is True


def test_bad_jobs_reach_health_and_inbox_once(monkeypatch):
    import datetime as dt
    import health
    jobs.mark("tokens", False, "boom")
    row = health.check_jobs()
    assert row["name"] == "Background jobs" and row["level"] == "bad" and "tokens" in row["detail"]
    assert health.alert([row], dt.datetime.now(), lambda *a, **k: (_ for _ in ()).throw(AssertionError("health must not post")))  is None
    jobs._alerted.clear()
    sent = []
    now = dt.datetime(2026, 11, 5, 8, 0)
    rows = jobs.listing()
    jobs.alert(rows, now, lambda *a, **k: sent.append(a))
    jobs.alert(rows, now, lambda *a, **k: sent.append(a))
    assert len(sent) == 1 and "tokens" in sent[0][1]
    jobs.mark("tokens", True)
    jobs.alert(jobs.listing(), now, lambda *a, **k: sent.append(a))
    jobs.mark("tokens", False, "again")
    jobs.alert(jobs.listing(), now, lambda *a, **k: sent.append(a))
    assert len(sent) == 2


def test_health_jobs_row_is_ok_when_all_fine():
    import health
    assert health.check_jobs()["level"] in ("ok", "warn")
    assert health.check_jobs in health.CHECKS


def test_thread_heartbeats_write_marks_and_rows_exist(monkeypatch):
    names = {s[0] for s in jobs.MARKED}
    assert {"mind", "corner_graph", "brief_scheduler", "servers"} <= names
    import briefs
    import servers
    monkeypatch.setattr(servers, "discover", lambda: None)
    monkeypatch.setattr(servers.threading, "Thread", lambda target, **kw: type("T", (), {"start": staticmethod(target)})())
    servers.state["running"] = False
    servers.refresh()
    assert jobs._marks["servers"]["ok"] is True
    import inspect
    import mind
    assert 'jobs.mark("mind"' in inspect.getsource(mind)
    assert 'jobs.mark("brief_scheduler"' in inspect.getsource(briefs)
    import corner_graph
    assert 'jobs.mark("corner_graph"' in inspect.getsource(corner_graph)


def test_dead_thread_rows_go_late_and_off_when_not_started(monkeypatch):
    import mind
    monkeypatch.setitem(mind.state, "started", False)
    assert by_name(jobs.listing())["mind"]["state"] == "off"
    monkeypatch.setitem(mind.state, "started", True)
    assert by_name(jobs.listing())["mind"]["state"] == "late"
    jobs.mark("mind", True)
    assert by_name(jobs.listing())["mind"]["state"] == "ok"


def test_alert_retries_after_a_failed_post():
    import datetime as dt
    rows = [{"name": "x", "what": "w", "state": "bad", "result": "r"}]
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("locked")

    jobs._alerted.clear()
    jobs.alert(rows, dt.datetime.now(), flaky)
    assert "x" not in jobs._alerted
    jobs.alert(rows, dt.datetime.now(), flaky)
    assert "x" in jobs._alerted and len(calls) == 2
    jobs.alert(rows, dt.datetime.now(), flaky)
    assert len(calls) == 2


def test_health_job_stays_ok_when_the_job_alert_fails(monkeypatch):
    import automations
    import health
    import datetime as dt
    monkeypatch.setattr(automations, "state", dict(automations.state, health_at=None))
    monkeypatch.setattr(health, "run_all", lambda: [])
    monkeypatch.setattr(health, "alert", lambda *a, **k: None)
    monkeypatch.setattr(automations.briefs, "log", lambda *a, **k: None)

    def boom(*a, **k):
        raise OSError("inbox locked")

    monkeypatch.setattr(jobs, "alert", boom)
    automations.job_health(dt.datetime.now(), {})
    assert jobs._marks["health"]["ok"] is True


def test_paper_with_nothing_built_is_marked_failed(monkeypatch):
    import automations
    import paper
    import datetime as dt
    monkeypatch.setattr(paper, "fake_loaded", lambda: False)
    monkeypatch.setattr(paper, "morning_due", lambda now: True)
    monkeypatch.setattr(paper, "claim_run", lambda day, now: True)
    monkeypatch.setattr(paper, "state", {"running": False})
    monkeypatch.setattr(automations.threading, "Thread", lambda target, **kw: type("T", (), {"start": staticmethod(target)})())
    monkeypatch.setattr(paper, "settings", lambda cfg=None: {"on": True})
    for built in (None, {}):
        jobs._marks.clear()
        monkeypatch.setattr(paper, "morning", lambda now=None, b=built: b)
        automations.job_paper(dt.datetime.now(), {})
        assert jobs._marks["paper"]["ok"] is False
        assert "no paper built" in jobs._marks["paper"]["err"]


def test_servers_row_goes_idle_not_late_when_stale():
    jobs.mark("servers", True)
    assert by_name(jobs.listing())["servers"]["state"] == "ok"
    age("servers", 3 * 86400)
    row = by_name(jobs.listing())["servers"]
    assert row["state"] == "off" and "idle" in row["result"]


def test_old_servers_failure_goes_idle_not_bad():
    jobs.mark("servers", False, err="scan failed")
    assert by_name(jobs.listing())["servers"]["state"] == "bad"
    age("servers", 3 * 86400)
    assert by_name(jobs.listing())["servers"]["state"] == "off"


def test_mark_redacts_secrets_in_the_error_text():
    key = "AIza" + "x" * 35
    jobs.mark("paper", False, err=f"HTTPError: GET https://example.test/v1?key={key} failed")
    err = jobs._marks["paper"]["err"]
    assert key not in err and "REDACTED" in err


def test_mark_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(jobs, "_save", boom)
    monkeypatch.setattr(jobs, "_load", boom)
    jobs.mark("x", True)
    jobs.mark("x", False, err=object())
    jobs.mark(None, True)
    jobs.mark(["unhashable"], True)


def test_mind_row_is_not_late_while_the_backfill_runs(monkeypatch):
    import mind
    import testguard
    monkeypatch.setattr(testguard, "active", lambda: False)
    monkeypatch.delenv("ROSIE_SANDBOX", raising=False)
    monkeypatch.delenv("ROSIE_MIND", raising=False)
    monkeypatch.setitem(mind.state, "started", False)
    seen = []

    def slow():
        seen.append(by_name(jobs.listing())["mind"]["state"])

    for n in ("repair_outside", "repair_briefs", "repair_pronouns", "repair_duplicates", "repair_sprawl"):
        monkeypatch.setattr(mind, n, lambda: None)
    monkeypatch.setattr(mind, "backfill", slow)
    monkeypatch.setattr(mind.threading, "Thread", lambda **kw: type("T", (), {"start": staticmethod(lambda: None)})())
    mind.start()
    assert seen == ["ok"]


def test_a_unit_that_is_not_installed_shows_not_installed():
    spec = ("rosie-sync", "timer", "Saves the code to GitHub", jobs.D)
    data = {"rosie-sync.timer": {"Id": "rosie-sync.timer", "LoadState": "not-found", "ActiveState": "inactive", "UnitFileState": ""}}
    row = jobs._unit_row(spec, data, time.time())
    assert row["state"] == "off" and row["result"] == "not installed"


def test_mark_failure_warning_is_rate_limited(monkeypatch, caplog):
    import logging

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(jobs, "_save", boom)
    monkeypatch.setattr(jobs, "_load", boom)
    monkeypatch.setattr(jobs, "_mark_warned", 0.0, raising=False)
    with caplog.at_level(logging.WARNING):
        for i in range(5):
            jobs.mark(f"x{i}", False, err="e")
    assert sum("jobs.mark failed" in r.getMessage() for r in caplog.records) == 1
