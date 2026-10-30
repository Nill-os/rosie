import json

import claude_plan


def test_state_that_is_not_a_dict_is_ignored(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text("[]")
    monkeypatch.setattr(claude_plan, "STATE", f)
    assert claude_plan._state() == {}


def test_an_unwritable_state_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_plan, "STATE", tmp_path / "nope" / "s.json")
    monkeypatch.setattr(claude_plan.testguard, "check", lambda p: None)
    monkeypatch.setattr(claude_plan.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    claude_plan._save(why="x")


def test_a_secret_cut_by_the_length_cap_is_redacted_first():
    key = "-----BEGIN PRIV" + "ATE KEY-----\n" + "A" * 200 + "\n-----END PRIV" + "ATE KEY-----"
    assert "AAAA" not in claude_plan._clean("x" * 10 + key, 60)
