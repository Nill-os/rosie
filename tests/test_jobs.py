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


def test_unit_rows_from_systemd(monkeypatch):
    now = time.time()
    data = {
        "rosie-backup.timer": {"Id": "rosie-backup.timer", "ActiveState": "active", "LastTriggerUSec": f"@{now - 100}"},
        "rosie-backup.service": {"Id": "rosie-backup.service", "Result": "exit-code"},
        "rosie-sync.timer": {"Id": "rosie-sync.timer", "ActiveState": "active", "LastTriggerUSec": f"@{now - 5 * 86400}"},
        "rosie-sync.service": {"Id": "rosie-sync.service", "Result": "success"},
        "assistant-dictation.service": {"Id": "assistant-dictation.service", "ActiveState": "inactive", "UnitFileState": "enabled", "Result": "success"},
        "assistant-overlay.service": {"Id": "assistant-overlay.service", "ActiveState": "inactive", "UnitFileState": "disabled", "Result": "success"},
        "rosie-phone.service": {"Id": "rosie-phone.service", "ActiveState": "active", "UnitFileState": "enabled", "Result": "success", "ActiveEnterTimestamp": f"@{now - 60}"},
    }
    monkeypatch.setattr(jobs.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(jobs.plat, "IS_WIN", False)
    monkeypatch.setattr(jobs, "_units", lambda: data)
    r = by_name(jobs.listing())
    assert r["rosie-backup"]["state"] == "bad"
    assert r["rosie-sync"]["state"] == "late"
    assert r["assistant-dictation"]["state"] == "bad"
    assert r["assistant-overlay"]["state"] == "off"
    assert r["rosie-phone"]["state"] == "ok"
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
    monkeypatch.setattr(automations.config, "load", lambda: {})
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


