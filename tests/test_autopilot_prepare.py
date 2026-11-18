import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import autopilot  # noqa: E402

ST = {"active": "A", "inactive": "B", "paths": {"A": "/x/a", "B": "/x/b"}, "heads": {"A": "1", "B": "1"}}


def fake_sh(seen, prepare_raises=False):
    def sh(cmd, cwd=None, timeout=300):
        seen.append((list(map(str, cmd)), timeout))
        if "prepare" in cmd:
            if prepare_raises:
                raise subprocess.TimeoutExpired(cmd, timeout)
            return subprocess.CompletedProcess(cmd, 0, '{"slot": "B"}', "")
        return subprocess.CompletedProcess(cmd, 1, "", "")
    return sh


def test_prepare_waits_longer_than_a_first_environment_build(monkeypatch):
    seen = []
    monkeypatch.setattr(autopilot, "sh", fake_sh(seen))

    class Stop(Exception):
        pass

    def git(*a):
        raise Stop

    monkeypatch.setattr(autopilot, "git", git)
    with pytest.raises(Stop):
        autopilot.run_audit({}, [], ST)
    prep = [t for c, t in seen if "prepare" in c]
    assert autopilot.PREPARE_TIMEOUT == 1500 and prep == [1500]
    print("PASS: prepare gets the named 1500 s timeout")


def test_a_prepare_timeout_is_logged_and_returns_like_a_refusal(monkeypatch):
    seen, logs = [], []
    monkeypatch.setattr(autopilot, "sh", fake_sh(seen, prepare_raises=True))
    monkeypatch.setattr(autopilot, "log", logs.append)
    assert autopilot.run_audit({}, [], ST) is None
    assert any("prepare timed out" in m and "1500" in m for m in logs)
    assert not any("audit start" in m for m in logs)
    print("PASS: a prepare timeout logs one line and returns")
