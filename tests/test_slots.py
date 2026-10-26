import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import slots  # noqa: E402
import watchdog  # noqa: E402


def sh(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    a, b, ops = tmp_path / "A", tmp_path / "B", tmp_path / "ops"
    a.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=a)
    (a / "app").mkdir()
    (a / "app" / "server.py").write_text("print('v1')\n")
    (a / ".gitignore").write_text("data/\nsecrets/\nvault/\n.venv/\n")
    for d in ("data", "secrets", "vault", ".venv"):
        (a / d).mkdir()
    (a / "data" / "tasks.db").write_text("precious")
    sh("git", "add", "-A", cwd=a)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v1", cwd=a)
    monkeypatch.setattr(slots, "OPS", ops)
    monkeypatch.setattr(slots, "STATE", ops / "state.json")
    monkeypatch.setattr(slots, "SLOT", {"A": a, "B": b})
    monkeypatch.setattr(slots, "restart_service", lambda: True)
    monkeypatch.setattr(slots, "healthz", lambda port=None, timeout=3: {"ok": True, "slot": slots.active()})
    monkeypatch.setattr(slots.time, "sleep", lambda s: None)
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.run(cmd, capture_output=True, text=True, cwd=kw.get("cwd")) if "backup.py" not in " ".join(map(str, cmd)) else subprocess.CompletedProcess(cmd, 0, "", ""))
    ops.mkdir()
    return {"a": a, "b": b, "ops": ops}


def edit_b(w, text):
    (w["b"] / "app" / "server.py").write_text(text)


def test_slot_b_is_a_clone_that_shares_the_state_not_a_copy_of_it(world):
    r = slots.ensure_b()
    assert (world["b"] / "app" / "server.py").exists()
    assert (world["b"] / "data").is_symlink() and (world["b"] / "data" / "tasks.db").read_text(encoding="utf-8") == "precious"
    assert (world["b"] / ".venv").is_symlink()
    sh("git", "add", "-A", cwd=world["b"])
    tracked = subprocess.run(["git", "ls-files"], cwd=world["b"], capture_output=True, text=True).stdout.split()
    assert not any(t.startswith(("data", "secrets", "vault", ".venv")) for t in tracked)  # board #896 safety-links-never-committed


def test_prepare_makes_b_an_exact_copy_and_snapshots_unsaved_live_changes(world):
    (world["a"] / "app" / "server.py").write_text("print('live edit not committed')\n")
    r = slots.prepare("add feature")
    assert r["slot"] == "B" and (world["b"] / "app" / "server.py").read_text(encoding="utf-8") == "print('live edit not committed')\n"
    assert r["branch"].startswith("rosie/add-feature") and slots.head("B") == slots.head("A")


def test_prepare_will_not_throw_away_unfinished_work_unless_forced(world):
    slots.prepare("one")
    edit_b(world, "print('work in progress')\n")
    r = slots.prepare("two")
    assert "uncommitted work" in r["error"] and (world["b"] / "app" / "server.py").read_text(encoding="utf-8") == "print('work in progress')\n"
    assert slots.prepare("two", force=True)["slot"] == "B" and (world["b"] / "app" / "server.py").read_text(encoding="utf-8") == "print('v1')\n"


def test_diff_and_commit(world):
    slots.prepare("x")
    edit_b(world, "print('v2')\nprint('more')\n")
    d = slots.diff()
    assert d["files"] == ["app/server.py"] and "1 file changed" in d["summary"]
    assert slots.commit("v2")["head"] != slots.head("A")


def test_switch_is_refused_until_the_exact_code_passed_the_full_check(world):
    slots.prepare("x")
    edit_b(world, "print('v2')\n")
    assert "not passed" in slots.switch()["error"] and slots.active() == "A"
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": "someoldcommit"})  # board #896 why-pass-needs-same-code
    assert "error" in slots.switch() and slots.active() == "A"
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    r = slots.switch()
    assert r["ok"] and slots.active() == "B" and slots.load_state()["previous"] == "A" and slots.load_state()["probation_until"] > time.time()


def test_a_dirty_candidate_cannot_slip_through_after_its_check(world):
    slots.prepare("x")
    edit_b(world, "print('v2')\n")
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    edit_b(world, "print('changed after the check')\n")  # board #896 why-recheck-after-edit
    assert "error" in slots.switch() and slots.active() == "A"


def test_if_the_new_version_never_comes_up_it_rolls_back_by_itself(world, monkeypatch):
    slots.prepare("x")
    edit_b(world, "print('broken')\n")
    slots.commit("broken")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    monkeypatch.setattr(slots, "healthz", lambda port=None, timeout=3: {"ok": True, "slot": "A"} if slots.active() == "A" else None)
    monkeypatch.setattr(slots, "wait_healthy", lambda slot, seconds=90: slot == "A")
    r = slots.switch()
    assert r["ok"] is False and r["rolled_back"] and slots.active() == "A"
    assert [e["kind"] for e in slots.events(10)][-1] == "rollback"


def test_manual_rollback_goes_back_and_switch_again_goes_forward(world):
    slots.prepare("x")
    edit_b(world, "print('v2')\n")
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    slots.switch()
    assert slots.rollback("user said go back")["active"] == "A" and slots.active() == "A"
    assert slots.load_state()["previous"] == "B"


def test_state_file_is_never_half_written(world):
    slots.save_state(a=1)
    slots.save_state(b=2)
    assert json.loads(slots.STATE.read_text(encoding="utf-8")) == {"a": 1, "b": 2} and not (world["ops"] / "state.tmp").exists()


def test_watchdog_rolls_back_only_after_repeated_failures_inside_the_probation(world, monkeypatch):
    slots.save_state(previous="A", probation_until=time.time() + 600, wd_fails=0)
    slots.set_active("B")
    rolled = []
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason: rolled.append(reason) or {"ok": True})
    monkeypatch.setattr(watchdog.slots, "healthz", lambda port=None, timeout=3: None)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *a, **k: None)
    for _ in range(2):
        watchdog.main()
    assert not rolled  # board #896 why-two-bad-minutes-tolerated
    watchdog.main()
    assert len(rolled) == 1 and "stopped answering" in rolled[0]


def test_watchdog_does_nothing_after_the_probation_or_when_healthy(world, monkeypatch):
    slots.save_state(previous="A", probation_until=time.time() - 5)
    monkeypatch.setattr(watchdog.slots, "healthz", lambda port=None, timeout=3: None)
    called = []
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason: called.append(1))
    watchdog.main()
    slots.save_state(probation_until=time.time() + 600)
    slots.set_active("B")
    monkeypatch.setattr(watchdog.slots, "healthz", lambda port=None, timeout=3: {"ok": True, "slot": "B"})
    watchdog.main()
    assert not called and slots.load_state()["wd_fails"] == 0


def test_launcher_falls_back_when_the_active_slot_is_unusable(tmp_path):
    ops = tmp_path / "ops"
    ops.mkdir()
    (ops / "active").write_text("B\n")
    a = tmp_path / "A" / "app"
    a.mkdir(parents=True)
    (a / "server.py").write_text("x")
    venv = tmp_path / "A" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "uvicorn").write_text("#!/bin/bash\necho STARTED-FROM $(pwd) SLOT=$ROSIE_SLOT\n")
    (venv / "uvicorn").chmod(0o755)
    launcher = ops / "launch.sh"
    launcher.write_text((Path(__file__).resolve().parent.parent / "ops" / "launch.sh").read_text(encoding="utf-8"))
    launcher.chmod(0o755)
    out = subprocess.run([str(launcher)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "ROSIE_SLOT_A": str(tmp_path / "A"), "ROSIE_SLOT_B": str(tmp_path / "missing"), "HOME": str(tmp_path)})
    assert f"STARTED-FROM {a} SLOT=A" in out.stdout and "unusable" in (ops / "events.log").read_text(encoding="utf-8")


def test_the_update_check_uses_the_model_rosie_chats_with(world):
    assert slots.chat_model() == "qwen3.5:9b"
    (world["a"] / "data" / "config.json").write_text(json.dumps({"chat_model": "huihui_ai/qwen3.5-abliterated:9b"}))
    assert slots.chat_model() == "huihui_ai/qwen3.5-abliterated:9b"  # board #896 why-chat-uses-settings-model
