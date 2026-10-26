# board #911 fix-windows-partner-issues
import subprocess
import types

import pytest

import a0_host
import agentdeck
import corner_pics
import gcal
import gmail
import health
import plat


def test_login_messages_name_the_command_that_exists_on_each_system(monkeypatch):
    monkeypatch.setattr(plat, "IS_WIN", False)
    assert plat.google_login() == "run ~/assistant/login.sh"
    assert plat.google_login("work", cap=True) == "Run ~/assistant/login-gmail.sh work"
    monkeypatch.setattr(plat, "IS_WIN", True)
    assert "Rosie Google Sign-in" in plat.google_login() and plat.google_login(cap=True).startswith("Open ")
    assert "login.sh" not in plat.google_login("work")


def test_gcal_login_expired_message_is_per_system(monkeypatch, tmp_path):
    monkeypatch.setattr(gcal, "TOKEN_FILE", tmp_path / "none.json")
    monkeypatch.setattr(plat, "IS_WIN", False)
    with pytest.raises(gcal.LoginExpired, match=r"Run \./login\.sh"):
        gcal.service()
    monkeypatch.setattr(plat, "IS_WIN", True)
    with pytest.raises(gcal.LoginExpired, match="Rosie Google Sign-in"):
        gcal.service()


def test_gmail_login_message_is_per_system(monkeypatch):
    monkeypatch.setattr(plat, "IS_WIN", True)
    with pytest.raises(gmail.MailLoginExpired, match="Rosie Google Sign-in"):
        gmail.service("nobody")


def test_linux_only_rows_are_off_not_problems_without_systemctl(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda n: None)
    rows = health.check_user_services()
    assert [r["level"] for r in rows] == ["off", "off"] and all("Linux only" in r["detail"] for r in rows)


def test_backup_and_code_sync_rows_are_info_on_windows(monkeypatch):
    monkeypatch.setattr(plat, "IS_WIN", True)
    monkeypatch.setattr(health.backup, "status", lambda: {"exists": False})
    r = health.check_backup()
    assert r["level"] == "off" and "Windows" in r["detail"]
    assert health.check_repo()["level"] == "off"


def test_the_agent_zero_row_uses_this_pcs_container(monkeypatch):
    seen = []

    def run(cmd, **k):
        seen.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "running\n", "")
    monkeypatch.setattr(a0_host, "container", lambda: "a0-inst-her-7")
    monkeypatch.setattr(health.subprocess, "run", run)
    rows = {r["name"]: r for r in health.check_containers()}
    assert seen[0] == "a0-inst-her-7" and rows["Agent Zero (her brain)"]["level"] == "ok"


def test_the_deck_asks_for_the_real_container_and_keeps_its_own_name(monkeypatch):
    monkeypatch.setattr(a0_host, "container", lambda: "a0-inst-her-7")
    asked = []

    def run(cmd, timeout):
        asked.append(cmd)
        return '[{"Name": "/a0-inst-her-7", "State": {"Status": "running"}}, {"Name": "/rosie-graph", "State": {"Status": "running"}}]'
    monkeypatch.setattr(agentdeck, "run", run)
    out = agentdeck._containers()
    assert asked[0][2:] == ["a0-inst-her-7", "rosie-graph"] and set(out) == {"agent-zero", "rosie-graph"}


def test_the_decks_chats_folder_comes_from_a0_host(monkeypatch, tmp_path):
    monkeypatch.setattr(a0_host, "usr", lambda: tmp_path / "usr")
    assert agentdeck._a0_chats() == tmp_path / "usr" / "chats"


def test_the_deck_snapshot_and_sample_do_not_raise_without_proc(monkeypatch):
    monkeypatch.setattr(agentdeck, "LINUX", False)
    monkeypatch.setattr(agentdeck, "WIN", None)  # board #911 quirk-agentdeck-psutil-readings
    monkeypatch.setattr(agentdeck, "AVAILABLE", False)
    assert "error" in agentdeck.snapshot()
    monkeypatch.setattr(agentdeck.os, "listdir", lambda p: (_ for _ in ()).throw(FileNotFoundError(p)))
    procs, _cores, _net, _up = agentdeck.sample()
    assert procs == {}


def _fake_scan_run(monkeypatch, seen):
    def run(cmd, **kw):
        seen.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "".join(f"{p}: OK\n" for p in cmd[cmd.index("--") + 1:]), "")
    monkeypatch.setattr(corner_pics.subprocess, "run", run)
    monkeypatch.setattr(corner_pics, "scanner", lambda: "clamscan")


def test_scan_runs_without_nice_when_the_system_has_none(monkeypatch):
    seen = []
    _fake_scan_run(monkeypatch, seen)
    monkeypatch.setattr(corner_pics.shutil, "which", lambda n: None)
    assert corner_pics.scan(["a.png"]) == {"a.png": "ok"}
    assert seen[0][0][0] == "clamscan"


def test_scan_keeps_nice_where_it_exists(monkeypatch):
    seen = []
    _fake_scan_run(monkeypatch, seen)
    monkeypatch.setattr(corner_pics.shutil, "which", lambda n: "/usr/bin/nice")
    monkeypatch.setattr(plat, "IS_WIN", False)
    corner_pics.scan(["a.png"])
    assert seen[0][0][:4] == ["nice", "-n", "19", "clamscan"] and not seen[0][1].get("creationflags")


def test_scan_on_windows_lowers_priority_with_a_process_flag(monkeypatch):
    seen = []
    _fake_scan_run(monkeypatch, seen)
    monkeypatch.setattr(corner_pics.shutil, "which", lambda n: None)
    monkeypatch.setattr(plat, "IS_WIN", True)
    monkeypatch.setattr(corner_pics.subprocess, "IDLE_PRIORITY_CLASS", 0x40, raising=False)
    corner_pics.scan(["a.png"])
    assert seen[0][1]["creationflags"] & 0x40


def test_scanner_looks_in_the_clamav_folder_on_windows_and_says_how_to_install(monkeypatch, tmp_path):
    exe = tmp_path / "ClamAV" / "clamscan.exe"
    exe.parent.mkdir()
    exe.write_text("x")
    monkeypatch.setattr(plat, "IS_WIN", True)
    monkeypatch.setattr(corner_pics.shutil, "which", lambda n: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert corner_pics.scanner() == str(exe)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nope"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "nope"))
    assert corner_pics.scanner() is None
    ok, why = corner_pics.ready()
    assert not ok and "winget install Cisco.ClamAV" in why
    monkeypatch.setattr(plat, "IS_WIN", False)
    assert corner_pics.ready() == (False, corner_pics.NO_SCANNER)
