import datetime as dt
import json
import time

import sys

import pytest

import backup
import health
import phone


_REAL_UNIT_RESULT = health._unit_result


@pytest.fixture(autouse=True)
def _no_real_units(monkeypatch):
    monkeypatch.setattr(health, "_unit_result", lambda unit: "")


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
    monkeypatch.setattr(health.plat, "IS_WIN", False)
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
    assert health.login_age_days("google") == 0.0
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
    assert sent[0][1] == "health:Backup:2026-10-04"


def test_disk_levels(monkeypatch):
    import shutil
    for gb, level in ((2, "bad"), (10, "warn"), (200, "ok")):
        monkeypatch.setattr(shutil, "disk_usage", lambda p, g=gb: type("U", (), {"free": g * 1e9})())
        assert health.check_disk()["level"] == level


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only: the user bus, systemd timers or Unix sockets")
def test_a_wayland_desktop_is_found_through_the_users_systemd(monkeypatch):
    # board #865 quirk-gnome-wayland-env
    import subprocess
    import pc_tools
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw.get("env", {})))
        if cmd[0] == "pgrep":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "LANG=en_US.UTF-8\nDISPLAY=:0\nWAYLAND_DISPLAY=wayland-0\nXDG_SESSION_TYPE=wayland\nSECRET=x\n", "")
    monkeypatch.setattr(pc_tools.subprocess, "run", run)
    env = pc_tools.gui_env()
    assert env["WAYLAND_DISPLAY"] == "wayland-0" and env["DISPLAY"] == ":0" and env["XDG_RUNTIME_DIR"].startswith("/run/user/")
    assert "SECRET" not in env and "LANG" not in env
    cmd, e = seen[-1]
    assert cmd == ["systemctl", "--user", "show-environment"] and e["DBUS_SESSION_BUS_ADDRESS"].endswith("/bus")
    monkeypatch.setattr(pc_tools.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
    assert pc_tools.gui_env() == {}


def test_status_shows_her_chat_model_not_the_embedder(monkeypatch):
    import agent
    import hub
    ps = {"models": [{"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest", "size": 300, "size_vram": 0},
                     {"name": agent.MODEL, "model": agent.MODEL, "size": 100, "size_vram": 100}]}
    monkeypatch.setattr(hub.httpx, "get", lambda *a, **k: type("R", (), {"json": lambda self: ps})())
    assert hub._model_info() == {"model": agent.MODEL, "loaded": True, "where": "GPU"}
    ps["models"] = ps["models"][:1]
    assert hub._model_info()["loaded"] is False


def test_containers_and_agent_zeros_line_are_checked(monkeypatch):
    import subprocess
    states = {"agent-zero": (0, "running\n", ""), "rosie-graph": (1, "", "Error: No such object: rosie-graph")}
    monkeypatch.setattr(health.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, *states[cmd[-1]]))

    def refuse(*a, **k):
        raise ConnectionRefusedError()
    import socket
    monkeypatch.setattr(socket, "create_connection", refuse)
    rows = {r["name"]: r for r in health.check_containers()}
    assert rows["Agent Zero (her brain)"]["level"] == "ok"
    assert rows["Memory graph database"]["level"] == "bad" and rows["Memory graph database"]["detail"] == "not found"
    assert rows["Agent Zero's line to Rosie"]["level"] == "bad"


def test_code_backup_and_backup_rows_follow_their_jobs_results(monkeypatch):
    monkeypatch.setattr(health.plat, "IS_WIN", False)
    import subprocess
    results = {"rosie-sync.service": "exit-code", "rosie-backup.service": "exit-code"}

    def run(cmd, **k):
        if cmd[:2] == ["systemctl", "--user"]:
            return subprocess.CompletedProcess(cmd, 0, results[cmd[-1]] + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, str(int(time.time())) + "\n" if "-1" in cmd else "", "")
    monkeypatch.setattr(health.subprocess, "run", run)
    monkeypatch.setattr(health, "_unit_result", _REAL_UNIT_RESULT)
    r = health.check_repo()
    assert r["level"] == "warn" and "last daily sync failed (exit-code)" in r["detail"]
    b = health.check_backup()
    assert b["level"] == "bad" and "off-PC copy" in b["detail"]
    results["rosie-sync.service"] = "success"
    assert health.check_repo()["level"] == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only: the user bus, systemd timers or Unix sockets")
def test_power_status_reaches_the_users_units(monkeypatch, tmp_path):
    import subprocess
    import power
    seen = {}
    script = tmp_path / "power.sh"
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(power, "SCRIPT", script)
    monkeypatch.setattr(power.subprocess, "run", lambda cmd, **k: (seen.update(k), subprocess.CompletedProcess(cmd, 0, "rosie active\n", ""))[1])
    assert power.status() == {"rosie": "active"} and seen["env"]["DBUS_SESSION_BUS_ADDRESS"]


def test_settings_voice_status_works_without_systemctl(monkeypatch):
    # board #865 quirk-windows-no-systemctl
    import hub
    import pc_tools
    monkeypatch.setattr(pc_tools, "gui_env", lambda: {})

    def no_systemctl(*a, **k):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr("subprocess.run", no_systemctl)
    st = hub.dictation_status()
    assert st["service"] == "unavailable" and st["overlay"] == "unavailable"


def test_windows_updates_row_follows_the_launchers_state(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    monkeypatch.setattr(health.plat, "IS_WIN", True)
    monkeypatch.setattr(health, "LAUNCHER_STATE", state)
    assert health.check_repo()["level"] == "off"  # board #865 why-no-state-file-off
    def put(age_days, result, head="abcdef1234"):
        state.write_text(json.dumps({"active": "A", "heads": {"A": head}, "last_check": time.time() - age_days * 86400, "last_result": result}))
        return health.check_repo()
    row = put(0.1, "Rosie is up to date.")
    assert row["name"] == "Updates" and row["level"] == "ok" and "abcdef1" in row["detail"]
    row = put(4, "Rosie is up to date.")
    assert row["level"] == "warn" and "up to date" in row["detail"]
    row = put(0.1, "Could not reach GitHub (URLError).")
    assert row["level"] == "warn" and "Could not reach GitHub" in row["detail"]
    assert put(0.1, "The new version (abc1234) failed GitHub's tests; waiting for a fixed one.")["level"] == "warn"
    state.write_text("not json")
    assert health.check_repo()["level"] == "off"
