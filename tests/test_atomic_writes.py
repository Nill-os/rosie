import json
import re
import stat
import sys
from pathlib import Path

import pytest

import atomic
import canvas
import model_scout
import security_watch
import servers
import tokens

APP = Path(__file__).resolve().parent.parent / "app"


SKIP = {"atomic.py"}


def test_no_module_hand_rolls_a_tmp_replace():
    bad = []
    for f in APP.glob("*.py"):
        if f.name in SKIP:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"os\.replace\(.*(tmp|\.tmp)", line) or re.search(r"\btmp\.replace\(", line):
                bad.append(f"{f.name}:{i}")
    assert bad == []


def test_write_text_replaces_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "a" / "x.json"
    atomic.write_text(p, "one")
    atomic.write_text(p, "two")
    assert p.read_text() == "two"
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX mode bits")
def test_write_text_sets_mode(tmp_path):
    p = tmp_path / "m.txt"
    atomic.write_text(p, "x", mode=0o640)
    assert stat.S_IMODE(p.stat().st_mode) == 0o640


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX mode bits")
def test_canvas_write_private_mode_and_content(tmp_path):
    p = tmp_path / "c.json"
    canvas._write(p, {"a": 1}, private=True)
    assert json.loads(p.read_text()) == {"a": 1}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    canvas._write(p, {"a": 2})
    assert stat.S_IMODE(p.stat().st_mode) == 0o644


def test_state_savers_use_atomic(tmp_path, monkeypatch):
    calls = []
    real = atomic.write_text
    monkeypatch.setattr(atomic, "write_text", lambda p, t, mode=None: (calls.append(Path(p).name), real(p, t, mode))[1])
    monkeypatch.setattr(model_scout, "STATE", tmp_path / "ms.json")
    monkeypatch.setattr(servers, "STATE", tmp_path / "sv.json")
    monkeypatch.setattr(security_watch, "STATE", tmp_path / "sw.json")
    monkeypatch.setattr(tokens, "STATE", tmp_path / "tk.json")
    model_scout.save({"seen": {}})
    servers.save({})
    security_watch.save({})
    tokens._save({"v": 2})
    assert sorted(calls) == ["ms.json", "sv.json", "sw.json", "tk.json"]
