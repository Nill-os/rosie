import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import slots  # noqa: E402
import watchdog  # noqa: E402

REAL_BUILD = slots._build_venv


def sh(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def fake_venv(slot):
    py = slots.venv_python(slot)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")


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
    monkeypatch.setattr(slots, "_build_venv", lambda slot: fake_venv(slot))
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.run(cmd, capture_output=True, text=True, cwd=kw.get("cwd")) if "backup.py" not in " ".join(map(str, cmd)) else subprocess.CompletedProcess(cmd, 0, "", ""))
    ops.mkdir()
    return {"a": a, "b": b, "ops": ops}


def edit_b(w, text):
    (w["b"] / "app" / "server.py").write_text(text)


def test_slot_b_is_a_clone_that_shares_the_state_not_a_copy_of_it(world):
    r = slots.ensure_b()
    assert (world["b"] / "app" / "server.py").exists()
    assert (world["b"] / "data").is_symlink() and (world["b"] / "data" / "tasks.db").read_text(encoding="utf-8") == "precious"
    assert not (world["b"] / ".venv").exists() and not (world["b"] / ".venv").is_symlink()
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


def idle_b(world):
    slots.ensure_b()
    return world["b"] / ".venv"


def recorder(calls, fail=False):
    def build(slot):
        calls.append(slot)
        fake_venv(slot)
        if fail:
            raise RuntimeError("pip exploded")
    return build


def test_a_shared_environment_the_live_slot_still_uses_is_left_alone(world, monkeypatch):
    venv = idle_b(world)
    venv.mkdir()
    (venv / "marker").write_text("live")
    shutil_rm = world["a"] / ".venv"
    shutil_rm.rmdir()
    shutil_rm.symlink_to(venv)
    calls = []
    monkeypatch.setattr(slots, "_build_venv", recorder(calls))
    r = slots.ensure_venv("B")
    assert r["venv"] == "shared" and r["note"]
    assert calls == [] and (venv / "marker").read_text() == "live" and not (venv / ".rosie-req").exists()
    print("PASS: an environment the live slot still links to is not touched")


def test_an_own_symlink_is_unlinked_without_touching_its_target(world, monkeypatch):
    venv = idle_b(world)
    target = world["a"].parent / "target"
    target.mkdir()
    (target / "marker").write_text("keep")
    venv.symlink_to(target)
    calls = []
    monkeypatch.setattr(slots, "_build_venv", recorder(calls))
    r = slots.ensure_venv("B")
    assert r["venv"] == "built" and calls == ["B"]
    assert not venv.is_symlink() and (target / "marker").read_text() == "keep"
    print("PASS: an own .venv symlink is unlinked and its target survives")


def test_a_matching_stamp_skips_the_build_and_a_changed_lock_rebuilds(world, monkeypatch):
    venv = idle_b(world)
    lock = world["b"] / "requirements.lock"
    lock.write_text("a==1\n")
    calls = []
    monkeypatch.setattr(slots, "_build_venv", recorder(calls))
    assert slots.ensure_venv("B")["venv"] == "built"
    stamp = (venv / ".rosie-req").read_text().strip()
    assert stamp == slots.req_stamp("B") and len(stamp) == 64
    assert slots.ensure_venv("B")["venv"] == "ok" and calls == ["B"]
    lock.write_text("a==2\n")
    assert slots.ensure_venv("B")["venv"] == "built" and calls == ["B", "B"]
    assert (venv / ".rosie-req").read_text().strip() == slots.req_stamp("B") != stamp
    (venv / ".rosie-req").unlink()
    assert slots.ensure_venv("B")["venv"] == "built" and len(calls) == 3
    print("PASS: the stamp decides between no-op and rebuild, and is written after the build")


def test_the_stamp_falls_back_to_the_two_requirement_files_without_a_lock(world):
    idle_b(world)
    (world["b"] / "requirements.txt").write_text("x\n")
    one = slots.req_stamp("B")
    (world["b"] / "requirements-test.txt").write_text("y\n")
    assert slots.req_stamp("B") != one
    print("PASS: without a lock the stamp follows both requirement files")


def test_the_live_slots_environment_is_never_rebuilt(world, monkeypatch):
    (world["a"] / ".venv" / "marker").write_text("live")
    calls = []
    monkeypatch.setattr(slots, "_build_venv", recorder(calls))
    r = slots.ensure_venv("A")
    assert "error" in r and calls == [] and (world["a"] / ".venv" / "marker").exists()
    print("PASS: ensure_venv refuses the active slot")


def test_a_failed_build_removes_the_half_built_folder_and_releases_the_claim(world, monkeypatch):
    venv = idle_b(world)
    posts = []
    monkeypatch.setattr(slots, "_board_call", lambda *a, **k: posts.append(a))
    monkeypatch.setattr(slots, "_build_venv", recorder([], fail=True))
    r = slots.ensure_venv("B")
    assert "pip exploded" in r["error"] and not venv.exists() and not (venv / ".rosie-req").exists()
    assert ("release", "slot-B") in posts
    print("PASS: a failed build leaves no folder and no stamp, and frees the slot")


def test_prepare_builds_the_idle_environment_and_stops_on_its_error(world, monkeypatch):
    r = slots.prepare("x")
    assert r["slot"] == "B" and r["venv"]["venv"] == "built" and slots.venv_python("B").exists()
    monkeypatch.setattr(slots, "_build_venv", recorder([], fail=True))
    (world["b"] / ".venv").rename(world["b"] / "gone")
    r = slots.prepare("y", force=True)
    assert "error" in r and "branch" not in r
    print("PASS: prepare returns the environment result and stops on its error")


def test_without_uv_the_pip_commands_are_chosen(world, monkeypatch):
    idle_b(world)
    (world["b"] / "requirements.lock").write_text("a==1 \\\n    --hash=sha256:00\n")
    monkeypatch.setattr(slots.shutil, "which", lambda n: None)
    cmds = slots._venv_commands("B")
    py = str(slots.venv_python("B"))
    assert cmds[0][1:3] == ["-m", "venv"] and cmds[0][-1] == str(world["b"] / ".venv")
    assert cmds[1] == [py, "-m", "pip", "install", "--require-hashes", "-r", str(world["b"] / "requirements.lock")]
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/x/uv")
    cmds = slots._venv_commands("B")
    assert cmds[0][:2] == ["/x/uv", "venv"] and "3.12" in cmds[0]
    assert cmds[1][:3] == ["/x/uv", "pip", "sync"] and "--require-hashes" in cmds[1] and py in cmds[1]
    print("PASS: the build commands follow uv or pip")


def test_the_stage_check_runs_in_the_idle_slots_environment(world, monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append((list(map(str, cmd)), kw.get("cwd")))
        return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")
    monkeypatch.setattr(slots, "run", run)
    monkeypatch.setattr(slots, "smoke", lambda idle: {"ok": True})
    slots._stage("B", False)
    py = str(world["b"] / ".venv" / "bin" / "python")
    tests = [c for c, _ in seen if "pytest" in c]
    assert len(tests) == 1 and py in tests[0] and py != str(world["a"] / ".venv" / "bin" / "python")
    assert all(py in c for c, _ in seen)
    popen = []

    class P:
        stderr = type("E", (), {"read": lambda self: b""})()
        poll = lambda self: 1
        terminate = wait = kill = lambda self, *a: None

    monkeypatch.setattr(slots, "healthz", lambda port=None, timeout=3: None)
    monkeypatch.setattr(slots.subprocess, "Popen", lambda cmd, **kw: popen.append(cmd) or P())
    monkeypatch.undo()
    print("PASS: stage uses the idle slot's python")


def test_the_smoke_server_runs_the_idle_slots_uvicorn(world, monkeypatch):
    slots.ensure_b()
    (world["b"] / "static").mkdir()
    popen = []

    class P:
        stderr = type("E", (), {"read": lambda self: b""})()
        poll = lambda self: 1
        terminate = wait = kill = lambda self, *a: None

    monkeypatch.setattr(slots, "healthz", lambda port=None, timeout=3: None)
    monkeypatch.setattr(slots.subprocess, "Popen", lambda cmd, **kw: popen.append(cmd) or P())
    slots.smoke("B")
    assert popen[0][0] == str(world["b"] / ".venv" / "bin" / "uvicorn")
    print("PASS: the smoke server uses the idle slot's uvicorn")


LAUNCH = Path(__file__).resolve().parent.parent / "ops" / "launch.sh"


def launch_world(tmp_path, active, with_b_venv):
    ops = tmp_path / "ops"
    ops.mkdir(parents=True)
    (ops / "launch.sh").write_text(LAUNCH.read_text(encoding="utf-8"))
    (ops / "active").write_text(active + "\n")
    for name, has in (("A", True), ("B", with_b_venv)):
        d = tmp_path / name
        (d / "app").mkdir(parents=True)
        (d / "app" / "server.py").write_text("")
        if has:
            u = d / ".venv" / "bin" / "uvicorn"
            u.parent.mkdir(parents=True)
            u.write_text("#!/bin/sh\necho \"$0\"\n")
            u.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ROSIE_SLOT_A": str(tmp_path / "A"), "ROSIE_SLOT_B": str(tmp_path / "B")}
    return subprocess.run(["bash", str(ops / "launch.sh")], env=env, capture_output=True, text=True).stdout.strip()


def test_launch_runs_the_active_slots_uvicorn_and_falls_back_to_slot_a(tmp_path):
    assert launch_world(tmp_path / "one", "B", True) == str(tmp_path / "one" / "B" / ".venv" / "bin" / "uvicorn")
    assert launch_world(tmp_path / "two", "B", False) == str(tmp_path / "two" / "A" / ".venv" / "bin" / "uvicorn")
    assert launch_world(tmp_path / "three", "A", True) == str(tmp_path / "three" / "A" / ".venv" / "bin" / "uvicorn")
    print("PASS: launch.sh uses the active slot's environment, else slot A's")


def pins(world, text):
    f = world["b"] / "data" / "venv_pins.txt"
    f.write_text(text)
    return f


def lock_b(world, text="a==1.0 \\\n    --hash=sha256:00\njinja2==3.1.2 \\\n    --hash=sha256:11\n"):
    (world["b"] / "requirements.lock").write_text(text)


def test_the_stamp_follows_the_shared_pins_file(world):
    idle_b(world)
    lock_b(world)
    base = slots.req_stamp("B")
    pins(world, "jinja2==3.1.6\n")
    one = slots.req_stamp("B")
    pins(world, "jinja2==3.1.7\n")
    assert base != one != slots.req_stamp("B")
    print("PASS: the stamp changes when the pins file changes")


def test_the_pins_install_after_the_lock_without_deps_or_hashes(world, monkeypatch):
    idle_b(world)
    lock_b(world)
    pins(world, "jinja2==3.1.6\n")
    py = str(slots.venv_python("B"))
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/x/uv")
    cmds = slots._venv_commands("B")
    assert len(cmds) == 3 and cmds[2] == ["/x/uv", "pip", "install", "--python", py, "--no-deps", "jinja2==3.1.6"]
    monkeypatch.setattr(slots.shutil, "which", lambda n: None)
    cmds = slots._venv_commands("B")
    assert cmds[2] == [py, "-m", "pip", "install", "--no-deps", "jinja2==3.1.6"] and "--require-hashes" not in cmds[2]
    (world["b"] / "data" / "venv_pins.txt").write_text("")
    assert len(slots._venv_commands("B")) == 2
    (world["b"] / "data" / "venv_pins.txt").unlink()
    assert len(slots._venv_commands("B")) == 2
    print("PASS: one extra no-deps command follows the lock, only when pins exist")


def test_a_hostile_pins_line_is_ignored(world, monkeypatch):
    idle_b(world)
    lock_b(world)
    before = slots.req_stamp("B")
    pins(world, "--index-url http://x\n-r /etc/passwd\na==1 --pre\n--pre\nb==2;x\n\n")
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/x/uv")
    assert len(slots._venv_commands("B")) == 2 and slots.req_stamp("B") == before
    pins(world, "--index-url http://x\njinja2==3.1.6\n-r /etc/passwd\na==1 --pre\n")
    cmds = slots._venv_commands("B")
    assert cmds[2] == ["/x/uv", "pip", "install", "--python", str(slots.venv_python("B")), "--no-deps", "jinja2==3.1.6"]
    print("PASS: lines that are not name==version are ignored")


def test_a_pin_the_lock_already_meets_is_dropped_at_build_time(world, monkeypatch):
    idle_b(world)
    lock_b(world)
    f = pins(world, "jinja2==3.1.1\na==1.0\nrequests==2.32.0\nA-Pkg==9\n")
    lock_b(world, "a==1.0 \\\n    --hash=sha256:00\njinja2==3.1.2 \\\n    --hash=sha256:11\nrequests==2.31.0 \\\n    --hash=sha256:22\n")
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/x/uv")
    cmds = slots._venv_commands("B")
    assert cmds[2][-2:] == ["requests==2.32.0", "A-Pkg==9"]
    ran = []
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: ran.append(cmd))
    monkeypatch.setattr(slots, "_build_venv", REAL_BUILD)
    slots._build_venv("B")
    assert f.read_text().split() == ["requests==2.32.0", "A-Pkg==9"] and ran[2][-2:] == ["requests==2.32.0", "A-Pkg==9"]
    print("PASS: pins at or below the lock are dropped from the file and the command")


def test_a_rebuild_after_pruning_keeps_the_same_stamp(world, monkeypatch):
    venv = idle_b(world)
    lock_b(world)
    pins(world, "jinja2==3.1.1\nrequests==2.32.0\n")
    monkeypatch.setattr(slots.shutil, "which", lambda n: "/x/uv")
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: fake_venv("B"))
    monkeypatch.setattr(slots, "_build_venv", REAL_BUILD)
    assert slots.ensure_venv("B")["venv"] == "built"
    assert (world["b"] / "data" / "venv_pins.txt").read_text() == "requests==2.32.0\n"
    assert slots.ensure_venv("B")["venv"] == "ok"
    print("PASS: pruning at build time does not trigger another rebuild")


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


def test_stage_tests_run_on_several_workers_only_when_the_plugin_is_there(monkeypatch):
    class R:
        def __init__(self, rc):
            self.returncode = rc
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: R(0))
    n = slots.stage_workers("/x/py")
    assert 2 <= n <= 8
    assert slots.pytest_command("/x/py", n)[-2:] == ["-n", str(n)]
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: R(1))
    assert slots.stage_workers("/x/py") == 0
    assert "-n" not in slots.pytest_command("/x/py", 0) and "-n" not in slots.pytest_command("/x/py")

    def boom(cmd, **kw):
        raise OSError("no python")
    monkeypatch.setattr(slots, "run", boom)
    assert slots.stage_workers("/x/py") == 0
    monkeypatch.setattr(slots.shutil, "which", lambda name: "/usr/bin/nice")
    monkeypatch.setattr(slots.os, "name", "nt")
    assert slots.gentle(["a", "b"]) == ["a", "b"]
    monkeypatch.setattr(slots.os, "name", "posix")
    assert slots.gentle(["a", "b"]) == ["/usr/bin/nice", "-n", "19", "a", "b"]
    monkeypatch.setattr(slots.shutil, "which", lambda name: None)
    assert slots.gentle(["a", "b"]) == ["a", "b"]
    print("PASS: the stage uses up to 8 test workers at low priority with the plugin and one process without it")


def test_the_build_has_one_time_budget_for_all_its_commands(world, monkeypatch):
    idle_b(world)
    monkeypatch.setattr(slots, "_venv_commands", lambda slot: [["one"], ["two"], ["three"]])
    clock = {"t": 0.0}
    monkeypatch.setattr(slots.time, "monotonic", lambda: clock["t"])
    seen = []

    def run(cmd, **kw):
        seen.append((cmd[0], kw["timeout"]))
        clock["t"] += 700
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(slots, "run", run)
    with pytest.raises(RuntimeError):
        REAL_BUILD("B")
    assert seen == [("one", 1200), ("two", 500)]
    assert slots.BUILD_BUDGET < 1500
    print("PASS: the build commands share one 1200 s budget, under the autopilot's wait")


def test_a_shared_environment_reports_what_differs_from_the_lock(world, monkeypatch):
    venv = idle_b(world)
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    (venv / "bin" / "python").write_text("")
    (world["b"] / "requirements.lock").write_text("a==1 \\\n    --hash=sha256:00\nB_b==2 \\\n    --hash=sha256:00\nc==3 ; sys_platform == 'win32' \\\n    --hash=sha256:00\nd==4 \\\n    --hash=sha256:00\n")
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "a==1\nb-b==2.1\n", ""))
    assert slots.env_drift("B") == ["B_b is 2.1, the lock wants 2", "d 4 is not installed"]
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "a==1\nb.b==2\nd==4\n", ""))
    assert slots.env_drift("B") == []
    monkeypatch.setattr(slots, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "x"))
    assert slots.env_drift("B") == ["the installed packages could not be listed"]
    print("PASS: drift between an environment and the lock is listed; platform-only packages are skipped")


def test_stage_rebuilds_the_environment_when_the_lock_changed_after_prepare(world, monkeypatch):
    venv = idle_b(world)
    lock = world["b"] / "requirements.lock"
    lock.write_text("a==1\n")
    calls, staged = [], []
    monkeypatch.setattr(slots, "_build_venv", recorder(calls))
    monkeypatch.setattr(slots, "claimed", lambda slot: "")
    monkeypatch.setattr(slots, "commit", lambda message="": {"ok": True})
    monkeypatch.setattr(slots, "_stage", lambda idle, with_eval: staged.append((venv / ".rosie-req").read_text().strip()) or {"ok": True})
    assert slots.ensure_venv("B")["venv"] == "built"
    assert slots.stage(False) == {"ok": True} and calls == ["B"]
    lock.write_text("a==2\n")
    assert slots.stage(False) == {"ok": True} and calls == ["B", "B"]
    assert staged[-1] == slots.req_stamp("B") != staged[0]
    monkeypatch.setattr(slots, "_build_venv", recorder(calls, fail=True))
    monkeypatch.setattr(slots, "release", lambda: {"ok": True})
    lock.write_text("a==3\n")
    r = slots.stage(False)
    assert r["ok"] is False and "environment failed" in r["error"] and len(staged) == 2
    assert slots.load_state()["last_stage"]["ok"] is False and "error" in slots.switch()
    print("PASS: stage rebuilds the environment after a lock change and stops when the build fails")


def test_the_lock_keeps_pip_so_a_built_environment_can_patch_itself():
    root = Path(slots.__file__).resolve().parent.parent
    lock = (root / "requirements.lock").read_text()
    assert any(ln.startswith("pip==") for ln in lock.splitlines())
    assert any(ln.split(">=")[0].strip() == "pip" for ln in (root / "requirements.txt").read_text().splitlines())
    print("PASS: pip is a locked requirement, so the self-patch and tool installs work in a synced environment")
