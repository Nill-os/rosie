import datetime as dt
import json
import time

import backup
import health
import phone


def test_summary_levels():
    assert health.summary([{"name": "a", "level": "ok"}])["level"] == "ok"
    assert health.summary([{"name": "a", "level": "warn"}, {"name": "b", "level": "ok"}])["level"] == "warn"
    s = health.summary([{"name": "a", "level": "warn"}, {"name": "b", "level": "bad"}])
    assert s["level"] == "bad" and s["bad"] == ["b"] and s["warn"] == ["a"]


def test_a_crashing_check_becomes_a_red_row_not_a_crash(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(health, "CHECKS", [boom, lambda: health._row("fine", "ok")])
    rows = health.run_all()
    assert rows[0]["level"] == "bad" and "kaboom" in rows[0]["detail"] and rows[1]["level"] == "ok"


def test_backup_check_levels(monkeypatch):
    for status, level in (({"exists": False}, "bad"),
                          ({"exists": True, "verified": False, "problems": ["x"], "age_hours": 1, "count": 1}, "bad"),
                          ({"exists": True, "verified": True, "age_hours": 50, "count": 3}, "bad"),
                          ({"exists": True, "verified": True, "age_hours": 30, "count": 3}, "warn"),
                          ({"exists": True, "verified": True, "age_hours": 5, "count": 3}, "ok")):
        monkeypatch.setattr(backup, "status", lambda s=status: s)
        assert health.check_backup()["level"] == level


def test_phone_link_levels(monkeypatch):
    monkeypatch.setattr(phone, "last_seen", 0.0)
    assert health.check_phone()["level"] == "off"
    monkeypatch.setattr(phone, "last_seen", time.time() - 3600)
    assert health.check_phone()["level"] == "warn"
    monkeypatch.setattr(phone, "last_seen", time.time() - 5)
    assert health.check_phone()["level"] == "ok"


def test_login_ageing_warns_before_google_ends_it(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "DATES", tmp_path / "dates.json")
    assert health.login_age_days("google") == 0.0                      # first sighting starts the clock
    old = (dt.datetime.now() - dt.timedelta(days=6)).isoformat()
    (tmp_path / "dates.json").write_text(json.dumps({"google": old}))
    r = health._aging("Google Calendar", "google", "Calendar", "run login.sh")
    assert r["level"] == "warn" and "6 days" in r["detail"]
    health.record_login("google")
    assert health._aging("Google Calendar", "google", "Calendar", "x") is None


def test_alerts_once_a_day_per_problem_and_only_for_real_problems():
    sent = []
    add = lambda kind, title, body, link, key, now: sent.append((title, key))
    now = dt.datetime(2026, 10, 4, 12, 0)
    rows = [{"name": "Backup", "level": "bad", "detail": "none", "fix": "run it"}, {"name": "Disk", "level": "warn", "detail": "25 GB free", "fix": ""},
            {"name": "Google Calendar", "level": "warn", "detail": "Calendar login is 6 days old; Google may end it at 7 days", "fix": "run login.sh"}, {"name": "Model", "level": "ok", "detail": "", "fix": ""}]
    health.alert(rows, now, add)
    assert [t for t, _ in sent] == ["Needs attention: Backup", "Needs attention: Google Calendar"]
    assert sent[0][1] == "health:Backup:2026-10-04"                    # same key all day: the inbox shows it once


def test_disk_levels(monkeypatch):
    import shutil
    for gb, level in ((2, "bad"), (10, "warn"), (200, "ok")):
        monkeypatch.setattr(shutil, "disk_usage", lambda p, g=gb: type("U", (), {"free": g * 1e9})())
        assert health.check_disk()["level"] == level
