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
    monkeypatch.setattr(slots, "NOTES_LOG", ops / "notes" / "Rosie update log.md")
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
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason, **k: rolled.append(reason) or {"ok": True})
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
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason, **k: called.append(1))
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


def _res(head, score, ok=True, failed=()):
    return {"slot": "B", "head": head, "ok": ok, "tests": {"ok": True, "summary": "10 passed"}, "smoke": {"ok": True},
            "eval": {"ok": ok, "score": score, "failed": list(failed)}}


def test_update_log_records_each_stage_and_compares_with_the_last_good_one(world):
    slots.record_update(_res("aaa1111", "12/13", failed=["FAIL clean 7.0s laundry"]), [5.0, 7.0, 3.0])
    slots.record_update(_res("bbb2222", "13/13"), [4.0, 4.0, 2.0])
    rows = slots.update_log_entries()
    assert rows[0]["vs_previous"] is None and rows[0]["median_s"] == 5.0
    assert rows[1]["vs_previous"] == {"head": "aaa1111", "score_change": 1, "median_change_s": -1.0}
    text = slots.NOTES_LOG.read_text()
    assert "aaa1111" in text and "bbb2222" in text and "+1 score, -1.0 s median vs aaa1111" in text


def test_update_log_handles_a_stage_without_eval_timings(world):
    slots.record_update(_res("ccc3333", "skipped"), [])
    assert slots.update_log_entries()[0]["median_s"] is None


def _unit_world(world, monkeypatch, calls, fail=False):
    units = world["a"] / "ops" / "units"
    units.mkdir(parents=True)
    (units / "one.service").write_text("[Unit]\n")
    (units / "two.service").write_text("[Unit]\n")
    sh("git", "add", "-A", cwd=world["a"])
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "units", cwd=world["a"])
    slots.prepare("x")
    edit_b(world, "print('v2')\n")
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/bin/systemctl")

    def fake(*args):
        calls.append((args, slots.active(), bool(slots.healthz())))
        if fail:
            raise RuntimeError("boom")

    monkeypatch.setattr(slots, "_systemctl", fake)


def test_switch_restarts_the_shipped_units_after_the_new_slot_is_healthy(world, monkeypatch):
    calls = []
    _unit_world(world, monkeypatch, calls)
    order = []
    monkeypatch.setattr(slots, "wait_healthy", lambda slot, seconds=90: order.append("healthy") or True)
    monkeypatch.setattr(slots, "_systemctl", lambda *a: order.append(a))
    assert slots.switch()["ok"]
    assert [o for o in order if o == "healthy" or o[0] == "try-restart"] == ["healthy", ("try-restart", "one.service"), ("try-restart", "two.service")]


def test_rollback_restarts_the_shipped_units_too(world, monkeypatch):
    calls = []
    _unit_world(world, monkeypatch, calls)
    slots.switch()
    calls.clear()
    slots.rollback("back")
    restarts = [c for c in calls if c[0][0] == "try-restart"]
    assert [c[0] for c in restarts] == [("try-restart", "one.service"), ("try-restart", "two.service")]
    assert all(c[1] == "A" for c in restarts)


def test_units_shipped_only_by_the_other_slot_are_still_restarted(world, monkeypatch):
    units = world["b"] / "ops" / "units"
    units.mkdir(parents=True)
    (units / "only-b.service").write_text("[Unit]\n")
    slots.set_active("A")
    assert not (world["a"] / "ops" / "units").exists()
    calls = []
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(slots, "_systemctl", lambda *a: calls.append(a))
    assert slots.restart_units() == ["only-b.service"]
    assert [c for c in calls if c[0] == "try-restart"] == [("try-restart", "only-b.service")]


def test_a_failing_systemctl_does_not_fail_the_switch(world, monkeypatch):
    calls = []
    _unit_world(world, monkeypatch, calls, fail=True)
    r = slots.switch()
    assert r["ok"] and slots.active() == "B" and len(calls) == 2


def test_a_unit_that_is_running_now_is_not_restarted(world, monkeypatch):
    import subprocess
    units = world["a"] / "ops" / "units"
    units.mkdir(parents=True)
    (units / "busy.service").write_text("[Unit]\n")
    (units / "idle.service").write_text("[Unit]\n")
    slots.set_active("A")
    calls = []

    def fake(*a):
        calls.append(a)
        if a[0] == "show":
            return subprocess.CompletedProcess(a, 0, "activating\n" if a[-1] == "busy.service" else "inactive\n", "")
        return subprocess.CompletedProcess(a, 0, "", "")
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/bin/systemctl")
    monkeypatch.setattr(slots, "_systemctl", fake)
    assert slots.restart_units() == ["idle.service"]
    assert ("try-restart", "busy.service") not in calls and ("try-restart", "idle.service") in calls


def _failed_rollback_world(world, monkeypatch):
    slots.save_state(previous="A", previous_head=slots.head("A"), previous_dirty=0, probation_until=time.time() + 600, wd_fails=0)
    slots.set_active("B")
    posts, notes = [], []
    monkeypatch.setattr(slots, "wait_healthy", lambda slot, seconds=90: False)
    monkeypatch.setattr(slots, "_board_call", lambda fn, *a, **k: posts.append((fn, a)))
    monkeypatch.setattr(slots, "restart_units", lambda: None)
    return posts


def test_a_rollback_to_a_dead_slot_keeps_the_probation_open_and_says_so(world, monkeypatch):
    posts = _failed_rollback_world(world, monkeypatch)
    r = slots.rollback("it died")
    st = slots.load_state()
    assert r["ok"] is False and r["probation_open"] is True
    assert st["probation_until"] > time.time() and st["wd_fails"] == 0
    assert slots.events(10)[-1]["kind"] == "rollback-unhealthy"
    assert any(fn == "post" and "not answering either" in a[0] for fn, a in posts)
    print("PASS: dead rollback keeps probation open")


def test_a_healthy_rollback_still_closes_the_probation(world, monkeypatch):
    _failed_rollback_world(world, monkeypatch)
    monkeypatch.setattr(slots, "wait_healthy", lambda slot, seconds=90: True)
    assert slots.rollback("back")["ok"] is True
    assert slots.load_state()["probation_until"] == 0


def test_watchdog_notifies_for_a_failed_rollback_too(world, monkeypatch):
    slots.save_state(previous="A", probation_until=time.time() + 600, wd_fails=2)
    slots.set_active("B")
    sent = []
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason, **k: {"ok": False, "probation_open": True})
    monkeypatch.setattr(watchdog.slots, "healthz", lambda port=None, timeout=3: None)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda cmd, **k: sent.append(cmd))
    watchdog.main()
    assert len(sent) == 1 and sent[0][0] == "notify-send" and "not answering" in sent[0][3] + sent[0][4]
    print("PASS: watchdog notifies on failed rollback")


def test_a_failed_backup_stops_the_switch_unless_forced(world, monkeypatch):
    slots.prepare("x")
    edit_b(world, "print('v2')\n")
    slots.commit("v2")
    slots.save_state(last_stage={"ok": True, "head": slots.head("B")})
    posts = []
    monkeypatch.setattr(slots, "_board_call", lambda *a, **k: posts.append(a))
    real = slots.run

    def run(cmd, **kw):
        if "backup.py" in " ".join(map(str, cmd)):
            if kw.get("check"):
                raise RuntimeError("gpg failed: no passphrase")
            return subprocess.CompletedProcess(cmd, 2, "", "gpg failed")
        return real(cmd, **kw)
    monkeypatch.setattr(slots, "run", run)
    r = slots.switch()
    assert r.get("backup_failed") and slots.active() == "A"
    assert any(p[0] == "post" and "backup" in p[1] for p in posts)
    assert slots.switch(force=True)["ok"] and slots.active() == "B"
    print("PASS: a failed backup stops the switch unless forced")


def test_a_second_failed_rollback_does_not_flip_again(world, monkeypatch):
    posts = _failed_rollback_world(world, monkeypatch)
    restarts = []
    monkeypatch.setattr(slots, "restart_service", lambda: restarts.append(1))
    assert slots.rollback("it died")["probation_open"] is True
    live = slots.active()
    r = slots.rollback("still dead", auto=True)
    st = slots.load_state()
    assert r["ok"] is False and r.get("gave_up") is True and "probation_open" not in r
    assert slots.active() == live and st["probation_until"] == 0
    assert slots.events(10)[-1]["kind"] == "rollback-gave-up"
    assert len(restarts) == 2
    print("PASS: second failed rollback stays put")


def test_a_new_switch_clears_the_gave_up_mark(world, monkeypatch):
    calls = []
    _unit_world(world, monkeypatch, calls)
    slots.save_state(rb_failed=True)
    assert slots.switch()["ok"]
    assert slots.load_state()["rb_failed"] is False


def test_watchdog_notifies_a_refused_rollback_once(world, monkeypatch):
    slots.save_state(previous="A", probation_until=time.time() + 600, wd_fails=2)
    slots.set_active("B")
    sent = []
    monkeypatch.setattr(watchdog.slots, "rollback", lambda reason, **k: {"ok": False, "error": "Slot A no longer holds the previous version"})
    monkeypatch.setattr(watchdog.slots, "healthz", lambda port=None, timeout=3: None)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda cmd, **k: sent.append(cmd))
    monkeypatch.setattr(watchdog.time, "sleep", lambda s: None)
    watchdog.main()
    watchdog.main()
    assert len(sent) == 1 and "Neither" not in sent[0][3] + sent[0][4]
    assert slots.load_state()["probation_until"] == 0
    print("PASS: refused rollback notified once")


def test_a_manual_rollback_still_goes_back_after_the_watchdog_gave_up(world, monkeypatch):
    _failed_rollback_world(world, monkeypatch)
    monkeypatch.setattr(slots, "restart_service", lambda: None)
    slots.rollback("it died")
    live = slots.active()
    monkeypatch.setattr(slots, "wait_healthy", lambda slot, seconds=90: True)
    r = slots.rollback("owner fixed it")
    assert r["ok"] is True and slots.active() != live and not slots.load_state()["rb_failed"]
    print("PASS: manual rollback goes back")


def test_stage_copy_of_a_database_includes_rows_only_in_the_wal(world, tmp_path):
    import sqlite3
    (world["a"] / "data" / "tasks.db").unlink()
    keep = sqlite3.connect(world["a"] / "data" / "tasks.db")
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE t (name TEXT)")
    keep.commit()
    keep.execute("INSERT INTO t VALUES ('Sam')")
    keep.commit()
    assert (world["a"] / "data" / "tasks.db-wal").stat().st_size > 0
    slots.SLOT["B"].mkdir()
    for d in ("app", "static"):
        (slots.SLOT["B"] / d).mkdir()
    stage = slots._build_stage("B")
    try:
        copy = sqlite3.connect(stage / "data" / "tasks.db")
        assert copy.execute("SELECT name FROM t").fetchall() == [("Sam",)]
        copy.close()
    finally:
        keep.close()
    print("PASS: stage copy includes WAL rows")


def test_stage_copy_falls_back_to_a_plain_copy_and_reads_odd_folder_names(world, tmp_path, monkeypatch):
    import sqlite3
    odd = tmp_path / "Sam #1 %41?x"
    odd.mkdir()
    real = sqlite3.connect(odd / "notes.db")
    real.execute("CREATE TABLE t (name TEXT)")
    real.execute("INSERT INTO t VALUES ('Sam')")
    real.commit()
    real.close()
    slots._copy_db(odd / "notes.db", tmp_path / "copy.db")
    copy = sqlite3.connect(tmp_path / "copy.db")
    assert copy.execute("SELECT name FROM t").fetchall() == [("Sam",)]
    copy.close()
    slots.SLOT["B"].mkdir()
    for d in ("app", "static"):
        (slots.SLOT["B"] / d).mkdir()
    stage = slots._build_stage("B")
    assert (stage / "data" / "tasks.db").read_text() == "precious"
    events = (slots.OPS / "events.jsonl").read_text()
    assert "stage-db-copy-fallback" in events and "tasks.db" in events
    print("PASS: stage copy reads odd folder names and falls back for non-sqlite files")


def test_stage_copy_fallback_keeps_wal_rows_and_survives_an_unwritable_log(world, tmp_path, monkeypatch):
    import sqlite3
    src = tmp_path / "w"
    src.mkdir()
    keep = sqlite3.connect(src / "notes.db")
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE t (name TEXT)")
    keep.commit()
    keep.execute("INSERT INTO t VALUES ('Sam')")
    keep.commit()

    def refuse(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")

    def no_log(*a, **k):
        raise OSError("read-only ops dir")

    monkeypatch.setattr(slots.sqlite3, "connect", refuse)
    monkeypatch.setattr(slots, "log_event", no_log)
    out = tmp_path / "out"
    out.mkdir()
    slots._copy_db(src / "notes.db", out / "notes.db")
    keep.close()
    monkeypatch.undo()
    copy = sqlite3.connect(out / "notes.db")
    assert copy.execute("SELECT name FROM t").fetchall() == [("Sam",)]
    copy.close()
    print("PASS: fallback copy keeps WAL rows and ignores an unwritable log")


def test_stage_copy_fallback_clears_a_stale_wal_next_to_the_copy(world, tmp_path, monkeypatch):
    import sqlite3
    src = tmp_path / "s"
    src.mkdir()
    db = sqlite3.connect(src / "notes.db")
    db.execute("CREATE TABLE t (name TEXT)")
    db.execute("INSERT INTO t VALUES ('Sam')")
    db.commit()
    db.close()
    out = tmp_path / "o"
    out.mkdir()
    (out / "notes.db-wal").write_bytes(b"stale")
    (out / "notes.db-shm").write_bytes(b"stale")

    def refuse(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(slots.sqlite3, "connect", refuse)
    slots._copy_db(src / "notes.db", out / "notes.db")
    monkeypatch.undo()
    assert not (out / "notes.db-wal").exists() and not (out / "notes.db-shm").exists()
    print("PASS: stale -wal and -shm next to the copy are removed")


def test_stage_pytest_command_reports_durations():
    cmd = slots.pytest_command("/x/py")
    assert cmd[:3] == ["/x/py", "-m", "pytest"]
    assert "--durations=30" in cmd and "-q" in cmd and "-x" in cmd
    print("PASS: stage pytest command asks for durations")


def test_stage_log_is_written_next_to_state_and_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, "STATE", tmp_path / "ops" / "state.json")
    slots.save_stage_log("first run\n")
    slots.save_stage_log("second run\n")
    p = tmp_path / "ops" / "last_stage_pytest.log"
    assert p.read_text() == "second run\n"
    print("PASS: stage log is overwritten beside the state file")
