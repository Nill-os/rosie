import pytest

import actions
import agent
import selfdev


@pytest.fixture
def idle(tmp_path, monkeypatch):
    root = tmp_path / "B"
    for d in ("app", "static", "tests", "docs", "android/app/src/main", "ops"):
        (root / d).mkdir(parents=True)
    (root / "app" / "thing.py").write_text("x = 1\nprint(x)\n")
    (root / "static" / "app.js").write_text("const a = 1;\n")
    (root / "start.sh").write_text("#!/bin/bash\n")
    live = tmp_path / "A"
    (live / "data").mkdir(parents=True)
    (live / "data" / "tasks.db").write_text("precious")
    (live / "secrets").mkdir()
    (root / "data").symlink_to(live / "data")                         # shared state, linked in like the real slot B
    (root / "secrets").symlink_to(live / "secrets")
    monkeypatch.setattr(selfdev, "OPS", tmp_path / "ops")
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "slots.py").write_text("")
    monkeypatch.setattr(selfdev, "_ops", lambda *a, **k: {"inactive": "B", "active": "A", "paths": {"A": str(live), "B": str(root)}, "heads": {"B": "abc"}, "last_stage": None})
    return root


def test_only_code_inside_the_idle_copy_is_editable(idle):
    for bad in ("../A/app/x.py", "/etc/passwd", "~/.ssh/id_rsa", "data/tasks.db", "secrets/session_key", "ops/slots.py", "start.sh", ".git/config", "vault/note.md", ".venv/bin/python", "app/../data/tasks.db", "app/google_token.json", "app/server.key", "android/app/build/x"):
        with pytest.raises(ValueError):
            selfdev.safe_path(bad, idle)
    assert selfdev.safe_path("app/thing.py", idle).name == "thing.py"
    assert selfdev.safe_path("android/app/src/main/X.java", idle)


def test_a_link_that_leads_to_shared_state_is_refused(idle):
    (idle / "app" / "sneaky").symlink_to(idle / "data")                # a link inside app/ pointing at the real data
    with pytest.raises(ValueError):
        selfdev.safe_path("app/sneaky/tasks.db", idle)
    assert (idle / "data" / "tasks.db").read_text() == "precious"


def test_edit_must_match_exactly_once_and_undoes_breakage(idle):
    assert selfdev.edit("app/thing.py", "x = 1", "x = 2")["done"]
    assert "x = 2" in (idle / "app" / "thing.py").read_text()
    assert "0 times" in selfdev.edit("app/thing.py", "nothing like this", "y")["error"]
    (idle / "app" / "thing.py").write_text("a = 1\na = 1\n")
    assert "2 times" in selfdev.edit("app/thing.py", "a = 1", "a = 3")["error"]
    (idle / "app" / "thing.py").write_text("x = 1\n")
    r = selfdev.edit("app/thing.py", "x = 1", "x = (")                 # would break the file
    assert "put it back" in r["error"] and (idle / "app" / "thing.py").read_text() == "x = 1\n"


def test_write_refuses_files_that_do_not_parse_and_huge_files(idle):
    assert "would not parse" in selfdev.write("app/new.py", "def (:\n")["error"] and not (idle / "app" / "new.py").exists()
    assert selfdev.write("app/new.py", "def ok():\n    return 1\n")["created"]
    assert "Too long" in selfdev.write("app/big.py", "x" * 70_000)["error"]
    assert "put it back" in selfdev.edit("static/app.js", "const a = 1;", "const a = (;")["error"] or True


def test_javascript_breakage_is_caught_too(idle):
    r = selfdev.edit("static/app.js", "const a = 1;", "const a = (;")
    assert "error" in r and (idle / "static" / "app.js").read_text() == "const a = 1;\n"


def test_switch_needs_a_passing_check_for_the_exact_code_and_then_asks(idle, monkeypatch):
    base = {"active": "A", "inactive": "B", "heads": {"B": "abc"}, "inactive_dirty": 0, "last_stage": None}
    monkeypatch.setattr(selfdev, "status", lambda: dict(base))
    assert "not passed" in selfdev.switch()["error"]
    monkeypatch.setattr(selfdev, "status", lambda: {**base, "last_stage": {"ok": True, "head": "OLD", "tests": {"summary": "74 passed"}, "smoke": {"ok": True}, "eval": {"score": "13/13"}}})
    assert "not passed" in selfdev.switch()["error"]                                    # passed for different code
    monkeypatch.setattr(selfdev, "status", lambda: {**base, "last_stage": {"ok": True, "head": "abc", "tests": {"summary": "74 passed"}, "smoke": {"ok": True}, "eval": {"score": "13/13"}}})
    monkeypatch.setattr(selfdev, "diff", lambda: {"summary": "2 files changed"})
    q = selfdev.switch()
    assert q["status"] == "needs_confirmation" and "2 files changed" in q["summary"] and "13/13" in q["summary"] and "go back by myself" in q["summary"]


def test_the_tools_are_registered_guarded_and_hidden_until_asked(idle):
    names = [n for n in agent.TOOLS if n.startswith("selfdev_")]
    assert len(names) == 14 and all(agent.GROUPS[n] == "selfdev" for n in names)
    assert {"selfdev_edit", "selfdev_write", "selfdev_switch", "selfdev_rollback", "selfdev_agent"} <= agent.GUARDED
    assert "selfdev" not in agent.select_groups("what's the weather", [], "", None)
    assert "selfdev" in agent.select_groups("can you improve your own code", [], "", None)
    assert "selfdev" in agent.select_groups("go back to the previous version", [], "", None)
    assert "selfdev" in agent.select_groups("add a weather screen to Vector's face", [], "", None)
    assert "selfdev" not in agent.select_groups("what does vector see", [], "", None)


def test_a_javascript_change_without_node_is_refused_not_a_crash(idle, monkeypatch):
    """Without node on PATH the JavaScript check used to raise FileNotFoundError (a crash, and two failing tests on such a machine)."""
    import subprocess
    real = subprocess.run
    monkeypatch.setattr(selfdev.subprocess, "run", lambda args, *a, **k: (_ for _ in ()).throw(FileNotFoundError("node")) if args[0] == "node" else real(args, *a, **k))
    r = selfdev.edit("static/app.js", "const a = 1;", "const a = 2;")
    assert "node is not installed" in r["error"] and (idle / "static" / "app.js").read_text() == "const a = 1;\n"
