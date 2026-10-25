"""Failure-injection tests for the Windows launcher, one group per approved risk (audit 2026-10-25, RISKS.md A1-A7).

Network, pip (uv), subprocess and time are faked. Scenarios other files already cover are not repeated:

| Risk | Already covered in                                                                                                   |
|------|----------------------------------------------------------------------------------------------------------------------|
| A1   | test_windows_launcher: test_a_failed_build_leaves_nothing_staged, test_switch_refuses_when_the_idle_slot_does_not_hold_the_staged_version, test_an_exception_in_the_loop_is_logged_and_the_loop_continues |
| A2   | test_windows_launcher: test_a_second_launcher_exits_and_opens_the_running_rosie, test_the_lock_is_refused_while_another_holder_has_it, test_no_rollback_before_she_answered_within_the_startup_grace, test_a_smoke_timeout_marks_the_version_bad_only_the_second_time |
| A3   | test_windows_launcher: test_three_build_failures_of_the_same_version_mark_it_bad, test_the_required_checks_are_the_workflows_job_ids; test_health: test_windows_updates_row_follows_the_launchers_state (old last_check and failed last_result warn) |
| A4   | test_windows_launcher: test_removing_a_slot_never_touches_the_shared_data, test_a_download_cannot_write_outside_its_slot |
| A5   | test_windows_launcher: test_relaunch_starts_a_new_process_with_the_same_arguments                                      |
| A7   | test_windows_launcher: test_only_a_commit_whose_every_check_passed_counts, test_the_required_jobs_must_all_exist_and_succeed |

A6 is review-only. Junction behavior itself runs only on Windows (skipif below); Linux uses the symlinks the launcher also handles.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_windows_launcher import SRC, GitHub, L, _Stop, zipball  # noqa: E402,F401  (L is the fixture)

REPO = Path(__file__).resolve().parent.parent
NEW = "new1"


class _Death(BaseException):
    """A simulated process death (power loss): not an Exception, so no `except Exception` in the launcher can absorb it."""


def job(n, c, status="completed"):
    return {"name": n, "status": status, "conclusion": c}


GOOD = [job("pytest", "success"), job("windows", "success"), job("windows-start", "success")]


@pytest.fixture
def env(L, monkeypatch):
    """Fake uv/pip: `uv venv` makes the venv's python file, `uv pip` does nothing."""
    monkeypatch.setattr(L, "uv", lambda: "uv")
    calls = []

    def run(args, **k):
        calls.append(args)
        if args[1] == "venv":
            p = Path(args[-1]) / ("Scripts/python.exe" if L.WIN else "bin/python")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(L.subprocess, "run", run)
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": True, "checks": {}})
    return calls


def make_active(L, head="old"):
    py = L.venv_python("A")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    st = L.load()
    st["heads"] = {"A": head}
    L.save(st)
    return L.load()


class Faulty(GitHub):
    def __init__(self, fail_on=None, exc=None, **kw):
        super().__init__(**kw)
        self.fail_on, self.exc = fail_on, exc

    def __call__(self, url, timeout=30):
        if self.fail_on and self.fail_on in url:
            raise self.exc
        return super().__call__(url, timeout)


class FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, t=None):
        return 0


def drive(L, monkeypatch, tmp_path, ticks=1, gh=None, probation=None, auto=True, quiet=True, real_rollback=False, healthz=None, popen=None):
    """Run run()'s loop for `ticks` passes on a fake clock. Returns (log lines, clock start). Raises whatever escapes run() except _Stop."""
    t0 = time.time()
    clock = [t0]
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "access_code").write_text("code")
    py = L.venv_python(L.load()["active"])
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    if probation is not None:
        L.save({**L.load(), "probation_until": t0 + probation})
    n = [0]

    def sleep(s):
        n[0] += 1
        if n[0] > ticks:
            raise _Stop
        clock[0] += s
    lines = []
    real_check = L.check
    monkeypatch.setattr(L.time, "sleep", sleep)
    monkeypatch.setattr(L.time, "time", lambda: clock[0])
    monkeypatch.setattr(L, "single_instance", lambda: True)
    monkeypatch.setattr(L, "first_run", lambda: None)
    monkeypatch.setattr(L, "sign_in_file", lambda: None)
    monkeypatch.setattr(L, "phone_address", lambda: None)
    monkeypatch.setattr(L, "healthz", healthz or (lambda *a, **k: {"ok": True}))
    monkeypatch.setattr(L, "quiet_now", lambda now=None: quiet)
    if gh is not None:
        monkeypatch.setattr(L, "check", lambda st, get=None: real_check(st, gh))
    if not real_rollback:
        monkeypatch.setattr(L, "rollback", lambda st, why: lines.append("ROLLBACK " + why))
    monkeypatch.setattr(L, "log", lines.append)
    monkeypatch.setattr(L, "settings", lambda: {**L.DEFAULTS, "auto": auto})
    monkeypatch.setattr(L, "server_cmd", lambda *a: ["x"])
    monkeypatch.setattr(L.subprocess, "Popen", popen or (lambda *a, **k: FakeProc()))
    monkeypatch.setattr(L.sys, "argv", ["launcher.py", "--no-browser"])
    with pytest.raises(_Stop):
        L.run()
    return lines, t0


def stage_ready(L, sha=NEW):
    """Slot A live, slot B built and checked for `sha`, update waiting for a quiet PC."""
    st = make_active(L)
    py = L.venv_python("B")
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("")
    st["heads"]["B"] = sha
    st["staged"] = sha
    L.save(st)
    return L.load()


# ------------------------------------------------------------------ A1: an update never leaves her without a working Rosie
def _inject(L, monkeypatch, step, exc):
    gh = Faulty(checks={NEW: GOOD})
    gh.exc = exc
    if step == "head":
        gh.fail_on = "/commits/main"
    elif step == "checks":
        gh.fail_on = "/check-runs"
    elif step == "download":
        gh.fail_on = "codeload"
    elif step == "pip":
        def boom(args, **k):
            if args[1] == "pip":
                raise exc
            return subprocess.CompletedProcess(args, 0)
        real = L.subprocess.run

        def run(args, **k):
            return boom(args) if args[1] == "pip" else real(args, **k)
        monkeypatch.setattr(L.subprocess, "run", run)
    elif step == "smoke":
        def smoke(slot):
            raise exc
        monkeypatch.setattr(L, "smoke", smoke)
    return gh


def _assert_safe(L):
    now = L.load()
    assert now["active"] == "A" and L.venv_python("A").exists()
    if now["staged"]:
        assert now["heads"].get(L.other(now["active"])) == now["staged"]     # never staged without a matching build
    assert not L.switch(dict(now)) or now["staged"] == now["heads"].get("B")  # a switch only to a slot whose head == staged
    return now


@pytest.mark.parametrize("step", ["head", "checks", "download", "pip", "smoke"])
def test_A1_exception_at_each_update_step_keeps_the_active_slot_and_the_loop(L, env, monkeypatch, tmp_path, step):
    """An ordinary exception at <step> of the update leaves slot A live with its venv and the run loop alive."""
    make_active(L)
    gh = _inject(L, monkeypatch, step, OSError("boom"))
    lines, _ = drive(L, monkeypatch, tmp_path, ticks=2, gh=gh)          # two passes: the loop survives the failure
    _assert_safe(L)
    assert not any(x.startswith("ROLLBACK") for x in lines)


@pytest.mark.parametrize("step", ["head", "checks", "download", "pip", "smoke"])
def test_A1_process_death_at_each_update_step_then_restart_recovers(L, env, monkeypatch, tmp_path, step):
    """Power loss during <step> (state as saved so far, next step never ran): slot A is intact and a restarted launcher finishes the update."""
    make_active(L)
    gh = _inject(L, monkeypatch, step, _Death("power"))
    with pytest.raises(_Death):
        L.check(L.load(), gh)
    now = _assert_safe(L)
    assert now["staged"] is None                                       # nothing waits for a build that did not finish
    # the "restart": same folders, fault gone
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": True, "checks": {}})
    monkeypatch.setattr(L.subprocess, "run", lambda args, **k: (L.venv_python("B").parent.mkdir(parents=True, exist_ok=True), L.venv_python("B").write_text(""), subprocess.CompletedProcess(args, 0))[2])
    L.check(L.load(), Faulty(checks={NEW: GOOD}))
    now = L.load()
    assert now["active"] == "A" and now["staged"] == NEW and now["heads"]["B"] == NEW and L.can_switch(now)


def test_A1_death_after_staged_is_cleared_leaves_nothing_staged(L, env, monkeypatch):
    """Death inside the build (after the old staged version was cleared) leaves staged empty on disk, so no stale switch."""
    st = stage_ready(L, "older")
    gh = Faulty(fail_on="codeload", exc=_Death("power"), checks={NEW: GOOD})
    with pytest.raises(_Death):
        L.check(st, gh)
    now = L.load()
    assert now["staged"] is None and now["active"] == "A"
    assert not L.switch(now)


STARTUP_OSERROR = pytest.param("startup", OSError("disk full"))   # was a strict xfail; fixed 2026-10-25


@pytest.mark.parametrize("where,exc", [("loop", OSError("disk full")), ("loop", _Death("power")), STARTUP_OSERROR, ("startup", _Death("power"))])
def test_A1_fault_while_switching_keeps_the_old_slot_live(L, env, monkeypatch, tmp_path, where, exc):
    """A fault saving the switch (exception or death before the state is written), at start-up or in the loop, leaves A active; the next clean pass switches."""
    stage_ready(L)
    real_save = L.save

    def save(st):
        if st.get("active") == "B":
            raise exc
        real_save(st)
    monkeypatch.setattr(L, "save", save)
    kw = dict(gh=Faulty(checks={NEW: GOOD}), probation=10 if where == "loop" else None, ticks=2)
    if isinstance(exc, _Death):
        with pytest.raises(_Death):
            drive(L, monkeypatch, tmp_path, **kw)
    else:
        drive(L, monkeypatch, tmp_path, **kw)
    disk = L.load()
    assert disk["active"] == "A" and L.venv_python("A").exists() and disk["staged"] == NEW
    monkeypatch.setattr(L, "save", real_save)
    assert L.switch(disk) and L.load()["active"] == "B" and L.load()["heads"]["B"] == NEW


def test_A1_a_stale_staged_version_is_never_switched_to(L, env, monkeypatch, tmp_path):
    """Staged names a version that slot B no longer holds (overwritten): switch refuses, at start-up and in the loop, and A stays live."""
    st = stage_ready(L)
    st["heads"]["B"] = "something-else"
    L.save(st)
    assert not L.switch(L.load())
    drive(L, monkeypatch, tmp_path, ticks=2)
    now = L.load()
    assert now["active"] == "A" and now["staged"] is None


def test_A1_staged_without_the_idle_venv_is_never_switched_to(L, env, monkeypatch, tmp_path):
    """Death mid-build left slot B without its venv while staged still names the sha: A stays live."""
    st = stage_ready(L)
    L.venv_python("B").unlink()
    assert not L.switch(L.load())
    drive(L, monkeypatch, tmp_path, ticks=2)
    assert L.load()["active"] == "A"


RELAUNCH_LOOP = pytest.param("loop", OSError("no process"))   # was a strict xfail; fixed 2026-10-25
RELAUNCH_STARTUP = pytest.param("startup", OSError("no process"))   # was a strict xfail; fixed 2026-10-25


@pytest.mark.parametrize("where,exc", [RELAUNCH_LOOP, ("loop", _Death("power")), RELAUNCH_STARTUP, ("startup", _Death("power"))])
def test_A1_fault_at_relaunch_keeps_a_working_slot_and_rosie_starts_again(L, env, monkeypatch, tmp_path, where, exc):
    """The switch is saved, then the new launcher cannot start (or the PC dies): the new slot is live with its venv, A is kept, Rosie is restarted."""
    stage_ready(L)
    newer = L.slot_dir("B") / "tools" / "windows"
    newer.mkdir(parents=True)
    (newer / "launcher.py").write_text("# newer launcher\n")
    me = tmp_path / "launcher.py"
    me.write_text("# older\n")
    monkeypatch.setattr(L, "__file__", str(me))
    started = []

    def popen(args, **k):
        if args[0] == sys.executable:
            raise exc
        started.append(args)
        return FakeProc()
    gh = Faulty(checks={NEW: GOOD})
    if isinstance(exc, _Death):
        with pytest.raises(_Death):
            drive(L, monkeypatch, tmp_path, ticks=2, gh=gh, popen=popen, probation=10 if where == "loop" else None)
    else:
        drive(L, monkeypatch, tmp_path, ticks=4, gh=gh, popen=popen, probation=10 if where == "loop" else None)
    disk = L.load()
    assert disk["active"] == "B" and L.venv_python("B").exists() and disk["heads"]["B"] == NEW
    assert disk["previous"] == "A" and L.venv_python("A").exists()
    if not isinstance(exc, _Death):
        assert started[-1] == ["x"] and len(started) >= (2 if where == "loop" else 1), "Rosie was not started again after the failed relaunch"


# ------------------------------------------------------------------ A2: a good update is never rolled back or marked bad by mistake
def test_A2_a_second_launcher_exits_without_starting_rosie_or_touching_state(L, env, monkeypatch, tmp_path):
    """A second launcher (lock held) exits 0 and starts nothing, runs no set-up, and leaves state.json and the log untouched."""
    st = make_active(L)
    st["probation_until"] = time.time() + 1000
    L.save(st)
    before = (L.STATE.read_bytes(), L.STATE.stat().st_mtime_ns)
    monkeypatch.setattr(L, "single_instance", lambda: False)
    monkeypatch.setattr(L, "healthz", lambda *a, **k: None)
    for name in ("first_run", "sign_in_file", "start_dependencies", "switch", "rollback", "check"):
        monkeypatch.setattr(L, name, lambda *a, **k: pytest.fail("a second launcher must do nothing"))
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("a second launcher must not start Rosie"))
    with pytest.raises(SystemExit) as e:
        L.run()
    assert e.value.code == 0
    assert (L.STATE.read_bytes(), L.STATE.stat().st_mtime_ns) == before and not L.LOG.exists()


def test_A2_a_second_launcher_with_the_real_lock_exits_without_starting_rosie(L, env, monkeypatch):
    """The real lock held by another process makes run() exit 0 before it loads state or starts anything."""
    if L.WIN:
        pytest.skip("msvcrt locking is covered on Windows CI")
    import fcntl
    holder = open(L.ROOT / "launcher.lock", "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    monkeypatch.setattr(L, "healthz", lambda *a, **k: None)
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("started Rosie"))
    try:
        with pytest.raises(SystemExit) as e:
            L.run()
        assert e.value.code == 0 and not L.STATE.exists()
    finally:
        holder.close()


def test_A2_probation_does_not_roll_back_before_she_answered_within_the_startup_grace(L, env, monkeypatch, tmp_path):
    """4.5 minutes of silence right after a switch, with the real rollback: no rollback, active slot and bad list unchanged."""
    st = make_active(L)
    st.update(previous="B", active="A")
    L.venv_python("B").parent.mkdir(parents=True, exist_ok=True)
    L.venv_python("B").write_text("")
    L.save(st)
    lines, _ = drive(L, monkeypatch, tmp_path, ticks=18, probation=99999, auto=False, real_rollback=True, healthz=lambda *a, **k: None)
    now = L.load()
    assert now["active"] == "A" and now["bad"] == [] and now["previous"] == "B"
    assert not any("went back" in x for x in lines)


def test_A2_a_single_smoke_timeout_does_not_mark_the_version_bad(L, env, monkeypatch):
    """One 'no answer in 2 minutes' smoke result leaves the sha out of the bad list and nothing staged; it is retried."""
    make_active(L)
    monkeypatch.setattr(L, "build", lambda *a, **k: None)
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": False, "checks": {}, "error": L.SMOKE_TIMEOUT})
    L.check(L.load(), Faulty(checks={NEW: GOOD}))
    now = L.load()
    assert NEW not in now["bad"] and now["staged"] is None and now["active"] == "A"
    gh = Faulty(checks={NEW: GOOD})
    L.check(L.load(), gh)                                                  # the next check still looks at it
    assert any("codeload" in u or "check-runs" in u for u in gh.calls)


# ------------------------------------------------------------------ A3: updates never stop without anyone being told
def test_A3_the_workflow_job_ids_are_exactly_the_required_checks():
    """tests.yml job ids == REQUIRED_CHECKS, so a renamed CI job cannot silently block (or bypass) every update."""
    wf = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    ids = set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", wf.split("\njobs:", 1)[1], re.M))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rosie_launcher_a3", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert ids == mod.REQUIRED_CHECKS


def test_A3_the_same_build_failure_three_times_marks_the_sha_bad_and_stops_retrying(L, env, monkeypatch):
    """Three build failures of one sha add it to bad; a fourth check makes no download and says so in last_result."""
    make_active(L)

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["uv", "pip", "install"])   # a broken build (network trouble never counts)
    monkeypatch.setattr(L, "build", boom)
    for _ in range(3):
        L.check(L.load(), Faulty(checks={NEW: GOOD}))
    assert NEW in L.load()["bad"]
    gh = Faulty(checks={NEW: GOOD})
    L.check(L.load(), gh)
    assert not any("check-runs" in u or "codeload" in u for u in gh.calls)
    assert "failed here before" in L.load()["last_result"]


def test_A3_check_errors_are_recorded_for_the_health_page(L, env, monkeypatch):
    """GitHub unreachable or its checks unreadable: last_check is now and last_result starts 'Could not', which the Updates row warns on."""
    make_active(L)
    for fail_on, text in (("/commits/main", "Could not reach GitHub"), ("/check-runs", "Could not read GitHub's checks")):
        before = time.time()
        L.check(L.load(), Faulty(fail_on=fail_on, exc=OSError("down"), checks={NEW: GOOD}))
        now = L.load()
        assert now["last_result"].startswith(text) and now["last_check"] >= before


def test_A3_a_crashing_check_is_retried_at_the_next_check_time_not_every_pass(L, env, monkeypatch, tmp_path):
    """check() raising inside the loop sets last_check to now, so it is called once, not every 15 seconds."""
    make_active(L)
    calls = []

    def boom(st, get=None):
        calls.append(1)
        raise RuntimeError("bug")
    monkeypatch.setattr(L, "check", boom)
    drive(L, monkeypatch, tmp_path, ticks=5)
    assert len(calls) == 1 and L.load()["last_check"] is not None


# ------------------------------------------------------------------ A4: updates and rollbacks never touch her data, secrets or vault
def _canaries(L):
    out = {}
    for n in L.SHARED:
        d = L.ROOT / n
        d.mkdir(exist_ok=True)
        f = d / "canary.txt"
        f.write_text(f"keep {n}\n")
        out[f] = f.read_text()
    return out


def _assert_canaries(L, canaries):
    for f, text in canaries.items():
        assert f.read_text() == text, f
        assert sorted(p.name for p in f.parent.iterdir()) == ["canary.txt"], f"{f.parent.name} changed"


def _slot_files():
    return {"app/server.py": "x = 1\n", "requirements.txt": "", "data/from_zip.txt": "zip", "vault/from_zip.txt": "zip", "secrets/from_zip.txt": "zip"}


def test_A4_update_builds_and_slot_rebuilds_leave_the_shared_folders_alone(L, env):
    """Build A, build B, rebuild A and B again (junction/symlink slots replaced): canary files in data, secrets and vault are unchanged."""
    canaries = _canaries(L)
    gh = GitHub(files=_slot_files())
    for slot in ("A", "B", "A", "B", "A"):
        L.build(slot, "new1", gh)
        _assert_canaries(L, canaries)
        for n in L.SHARED:
            assert L._is_link(L.slot_dir(slot) / n)
            assert (L.slot_dir(slot) / n / "canary.txt").read_text() == canaries[L.ROOT / n / "canary.txt"]


def test_A4_a_leftover_new_folder_with_links_is_cleared_without_touching_the_targets(L, env):
    """A death after linking left .B-new with links to the shared folders: the next build removes it and the canaries survive."""
    canaries = _canaries(L)
    tmp = L.SLOTS / ".B-new"
    tmp.mkdir(parents=True)
    for n in L.SHARED:
        if L.WIN:
            import _winapi
            _winapi.CreateJunction(str(L.ROOT / n), str(tmp / n))
        else:
            (tmp / n).symlink_to(L.ROOT / n, target_is_directory=True)
    L.build("B", "new1", GitHub(files=_slot_files()))
    _assert_canaries(L, canaries)


def test_A4_a_download_into_a_slot_with_links_leaves_the_targets_alone(L, env):
    """download() empties an existing slot that has links first; a zip with data/ files never writes through them."""
    canaries = _canaries(L)
    L.build("A", "new1", GitHub(files=_slot_files()))
    L.download("new1", L.slot_dir("A"), GitHub(files=_slot_files()))
    _assert_canaries(L, canaries)


def test_A4_a_rollback_leaves_the_shared_folders_alone(L, env):
    """switch() then rollback() (and a rebuild of the bad slot) never changes the shared canaries."""
    canaries = _canaries(L)
    gh = GitHub(files=_slot_files())
    L.build("A", "old1", gh)
    L.build("B", "new1", gh)
    st = L.load()
    st.update(heads={"A": "old1", "B": "new1"}, staged="new1")
    L.save(st)
    assert L.switch(st)
    L.rollback(L.load(), "test")
    assert L.load()["active"] == "A" and "new1" in L.load()["bad"]
    _assert_canaries(L, canaries)
    L.build("B", "new2", gh)
    _assert_canaries(L, canaries)


def test_A4_removing_a_slot_unlinks_the_links_and_never_deletes_through_them(L, env):
    """_remove() takes the links out of a slot and keeps the targets, and the slot's own files go."""
    canaries = _canaries(L)
    L.build("A", "new1", GitHub(files=_slot_files()))
    slot = L.slot_dir("A")
    L._remove(slot)
    assert not slot.exists()
    _assert_canaries(L, canaries)


@pytest.mark.skipif(os.name != "nt", reason="real directory junctions exist only on Windows")
def test_A4_real_junctions_are_unlinked_before_the_slot_is_deleted(L, env):
    """On Windows a slot with real junctions to data, secrets and vault is deleted and rebuilt without touching the targets."""
    canaries = _canaries(L)
    gh = GitHub(files=_slot_files())
    L.build("A", "new1", gh)
    for n in L.SHARED:
        assert (L.slot_dir("A") / n).is_junction()
    L.build("A", "new2", gh)
    L._remove(L.slot_dir("A"))
    _assert_canaries(L, canaries)


# ------------------------------------------------------------------ A5: a launcher self-update always leaves a launcher running
def _self_update(L, monkeypatch, tmp_path):
    newer = L.slot_dir("A") / "tools" / "windows"
    newer.mkdir(parents=True)
    (newer / "launcher.py").write_text("# newer launcher\n")
    me = tmp_path / "launcher.py"
    me.write_text("# older\n")
    monkeypatch.setattr(L, "__file__", str(me))
    monkeypatch.setattr(L.sys, "argv", ["launcher.py"])
    return me


def test_A5_the_lock_is_released_before_the_new_launcher_starts_and_it_can_take_it(L, monkeypatch, tmp_path):
    """The old launcher closes its lock before Popen; at that moment a second lock attempt (the new launcher) succeeds."""
    if L.WIN:
        pytest.skip("msvcrt locking is covered on Windows CI")
    import fcntl
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    me = _self_update(L, monkeypatch, tmp_path)
    assert L.single_instance() is True
    seen = {}

    def popen(args, **k):
        probe = open(L.ROOT / "launcher.lock", "a+")
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["free"] = True
        except OSError:
            seen["free"] = False
        probe.close()
        seen["new_launcher_took_it"] = L.single_instance()
        seen["args"] = args
    monkeypatch.setattr(L.subprocess, "Popen", popen)
    with pytest.raises(SystemExit) as e:
        L.relaunch_if_launcher_changed({"active": "A"}, L.Runner())
    assert e.value.code == 0
    assert seen["free"] is True and seen["new_launcher_took_it"] is True and seen["args"][1] == str(me.resolve())
    L._LOCK.close()


def test_A5_the_old_launcher_keeps_the_lock_if_nothing_changed(L, monkeypatch, tmp_path):
    """With no newer launcher in the slot nothing is closed, copied or started."""
    me = _self_update(L, monkeypatch, tmp_path)
    (L.slot_dir("A") / "tools" / "windows" / "launcher.py").write_text(me.read_text())
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: pytest.fail("relaunched"))
    L.relaunch_if_launcher_changed({"active": "A"}, L.Runner())


def test_A5_a_relaunch_never_loops(L, monkeypatch, tmp_path):
    """After the copy the bytes are equal, so the new launcher's own check starts nothing; the copy equals the slot's file."""
    me = _self_update(L, monkeypatch, tmp_path)
    popens = []
    monkeypatch.setattr(L.subprocess, "Popen", lambda args, **k: popens.append(args))
    with pytest.raises(SystemExit):
        L.relaunch_if_launcher_changed({"active": "A"}, L.Runner())
    assert me.read_bytes() == (L.slot_dir("A") / "tools" / "windows" / "launcher.py").read_bytes()
    L.relaunch_if_launcher_changed({"active": "A"}, L.Runner())        # what the new launcher does at its own switch: no exit, no start
    assert len(popens) == 1


# ------------------------------------------------------------------ A7: only a commit whose every required job succeeded is installed
REQ = ("pytest", "windows", "windows-start")


def _runs(**over):
    """The three required jobs as successes, with `over` = {name: conclusion or ('queued'|'in_progress')} replacing them; None drops a job."""
    runs = {n: job(n, "success") for n in REQ}
    for n, v in over.items():
        n = n.replace("_", "-")
        if v is None:
            runs.pop(n, None)
        elif v in ("queued", "in_progress"):
            runs[n] = job(n, None, v)
        else:
            runs[n] = job(n, v)
    return list(runs.values())


A7_BAD = [(n, v, False if v is not None and v not in ("queued", "in_progress") or v is None else None)
          for n in REQ for v in (None, "skipped", "failure", "cancelled", "timed_out", "queued", "in_progress")]


@pytest.mark.parametrize("name,state,verdict", A7_BAD)
def test_A7_a_commit_with_a_required_job_missing_skipped_failed_cancelled_or_running_is_never_built(L, env, monkeypatch, name, state, verdict):
    """Required job <name> is <state>: checks_passed is False (or None while running) and check() never builds."""
    make_active(L)
    runs = _runs(**{name: state})
    gh = Faulty(checks={NEW: runs})
    assert L.checks_passed(NEW, gh) is verdict
    monkeypatch.setattr(L, "build", lambda *a, **k: pytest.fail("built a commit that did not pass every required job"))
    L.check(L.load(), gh)
    assert L.load()["staged"] is None and L.load()["active"] == "A"


@pytest.mark.parametrize("extra,allowed", [(job("lint", "skipped"), True), (job("lint", "neutral"), True), (job("lint", "failure"), False),
                                           (job("lint", "cancelled"), False), (job("lint", None, "in_progress"), False)])
def test_A7_only_a_skipped_extra_check_is_allowed(L, env, monkeypatch, extra, allowed):
    """An extra non-required check that is skipped (or neutral) is allowed; failed, cancelled or running is not."""
    make_active(L)
    built = []
    monkeypatch.setattr(L, "build", lambda slot, sha, get=None: built.append(sha))
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": True, "checks": {}})
    L.check(L.load(), Faulty(checks={NEW: _runs() + [extra]}))
    assert (built == [NEW]) is allowed


@pytest.mark.parametrize("where", ["loop", "startup"])
def test_A5_a_successful_relaunch_starts_no_second_rosie(L, env, monkeypatch, tmp_path, where):
    """Gate review 2026-10-25: the old launcher must exit after starting the new one, never start Rosie again on its way out (port clash)."""
    stage_ready(L)
    newer = L.slot_dir("B") / "tools" / "windows"
    newer.mkdir(parents=True)
    (newer / "launcher.py").write_text("# newer launcher\n")
    me = tmp_path / "launcher.py"
    me.write_text("# older\n")
    monkeypatch.setattr(L, "__file__", str(me))
    calls = []

    def popen(args, **k):
        calls.append("relaunch" if args[0] == sys.executable else "rosie")
        return FakeProc()
    with pytest.raises(SystemExit):
        drive(L, monkeypatch, tmp_path, ticks=4, gh=Faulty(checks={NEW: GOOD}), popen=popen, probation=10 if where == "loop" else None)
    assert calls[-1] == "relaunch", calls


def test_A2_a_slow_first_answer_in_the_smoke_test_counts_as_a_timeout(L, monkeypatch):
    """Pass-1 note 2026-10-25: a timeout on /api/home marked a good version bad at once; it now gets the second-time rule."""
    import urllib.error
    for d in ("app", "static"):
        (L.slot_dir("A") / d).mkdir(parents=True, exist_ok=True)

    class Alive:
        def poll(self):
            return None
        def terminate(self):
            pass
        def wait(self, t=None):
            return 0
    for exc in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
        seen = {}

        def popen(cmd, env=None, **k):
            seen["slot"] = env["ROSIE_SLOT"]
            return Alive()
        monkeypatch.setattr(L.subprocess, "Popen", popen)
        monkeypatch.setattr(L, "healthz", lambda port=None, timeout=3: {"slot": seen.get("slot")})

        def slow(*a, _e=exc, **k):
            raise _e
        monkeypatch.setattr(L.urllib.request, "urlopen", slow)
        assert L.smoke("A")["error"] == L.SMOKE_TIMEOUT, exc


# ------------------------------------------------------------------ A3: helpers from the old slot (her PC, 2026-10-25)
def test_A3_old_slot_helpers_are_matched_by_exact_folder(L):
    """Claude Code's Rosie helpers keep running from the old slot and lock it; only programs inside that exact slot folder match."""
    a, b = L.slot_dir("A"), L.slot_dir("B")
    listing = "\n".join([
        f"101\t{a}\\.venv\\Scripts\\python.exe\t\"{a}\\.venv\\Scripts\\python.exe\" \"{a}\\app\\board_mcp.py\"",
        f"102\tC:\\py\\python.exe\t\"C:\\py\\python.exe\" \"{a}/app/mind_mcp.py\"",
        f"103\t{b}\\.venv\\Scripts\\python.exe\t\"{b}\\.venv\\Scripts\\python.exe\" -m uvicorn server:app",
        f"104\t{a}B\\x.exe\t{a}B\\x.exe",
        f"{L.os.getpid()}\t{a}\\me.exe\t{a}\\me.exe",
        "garbage line",
    ])
    assert L._match_helpers(listing, "A") == [101, 102]


def test_A3_the_old_slots_helpers_are_stopped_before_it_is_rebuilt(L, monkeypatch):
    """A build of the idle slot first stops what still runs from it (WinError 32 on her PC); the live slot's programs are never touched."""
    st = L.load()
    st["heads"] = {"A": "old"}
    L.save(st)
    stopped, built = [], []
    monkeypatch.setattr(L, "helpers_in", lambda slot: [7, 8] if slot == "B" else [99])
    monkeypatch.setattr(L, "_kill_tree", lambda pid: stopped.append(pid))
    monkeypatch.setattr(L, "build", lambda slot, sha, get=None: built.append((slot, list(stopped))))
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": True, "checks": {}})
    L.check(L.load(), GitHub())
    assert built == [("B", [7, 8])] and 99 not in stopped
