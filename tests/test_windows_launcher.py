"""The Windows launcher's update logic (owner 2026-10-23: "i want ota updates so when i update rosie hers also updates with the slot
system"). GitHub is faked; the parts that only exist on Windows (junctions, idle time) are covered by the Windows CI job."""
import importlib.util
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "tools" / "windows" / "launcher.py"


@pytest.fixture
def L(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("rosie_launcher", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, p in (("ROOT", tmp_path), ("SLOTS", tmp_path / "slots"), ("STATE", tmp_path / "state.json"), ("LOG", tmp_path / "update.log"),
                    ("SETTINGS", tmp_path / "update.json")):
        monkeypatch.setattr(mod, name, p)
    (tmp_path / "slots").mkdir()
    return mod


def zipball(files: dict, top="rosie-abc") -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, text in files.items():
            z.writestr(f"{top}/{name}", text)
    return b.getvalue()


class GitHub:
    """Answers the few GitHub calls the launcher makes."""
    def __init__(self, head="new1", checks=None, files=None):
        self.head, self.calls = head, []
        self.checks = {"new1": [{"name": "pytest", "status": "completed", "conclusion": "success"},
                                {"name": "windows", "status": "completed", "conclusion": "success"}]} if checks is None else checks
        self.files = files or {"app/server.py": "x = 1\n", "requirements.txt": "", "tools/windows/launcher.py": SRC.read_text()}

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        if url.endswith("/commits/main"):
            return json.dumps({"sha": self.head}).encode()
        if "/check-runs" in url:
            sha = url.split("/commits/")[1].split("/")[0]
            return json.dumps({"check_runs": self.checks.get(sha, [])}).encode()
        if "codeload.github.com" in url:
            return zipball(self.files)
        raise AssertionError(url)


def test_only_a_commit_whose_every_check_passed_counts(L):
    gh = GitHub(checks={"a": [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "skipped"}],
                        "b": [{"status": "completed", "conclusion": "success"}, {"status": "in_progress", "conclusion": None}],
                        "c": [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "failure"}],
                        "d": []})
    assert L.checks_passed("a", gh) is True
    assert L.checks_passed("b", gh) is None          # still running
    assert L.checks_passed("c", gh) is False
    assert L.checks_passed("d", gh) is None          # no checks yet: wait


def test_a_new_passing_version_is_built_in_the_idle_slot_and_checked_before_it_is_staged(L, monkeypatch):
    built, smoked = [], []
    monkeypatch.setattr(L, "install_libraries", lambda slot: built.append(slot))
    monkeypatch.setattr(L, "smoke", lambda slot: (smoked.append(slot), {"ok": True, "checks": {}})[1])
    st = L.load()
    st["heads"]["A"] = "old"
    msg = L.check(st, GitHub())
    assert "built and tested" in msg
    st = L.load()
    assert st["staged"] == "new1" and st["heads"] == {"A": "old", "B": "new1"} and st["active"] == "A"
    assert built == ["B"] and smoked == ["B"]
    assert (L.SLOTS / "B" / "app" / "server.py").read_text() == "x = 1\n"
    assert (L.SLOTS / "B" / "VERSION").read_text().strip() == "new1"
    for name in L.SHARED:                                   # the slot links to the one shared copy of her data
        assert L._is_link(L.SLOTS / "B" / name) and (L.SLOTS / "B" / name).resolve() == (L.ROOT / name).resolve()


def test_nothing_happens_while_github_is_still_testing_or_the_tests_failed(L, monkeypatch):
    monkeypatch.setattr(L, "build", lambda *a, **k: pytest.fail("built an untested version"))
    st = L.load()
    st["heads"]["A"] = "old"
    assert "still being tested" in L.check(st, GitHub(checks={"new1": [{"status": "queued"}]}))
    assert "failed GitHub's tests" in L.check(L.load(), GitHub(checks={"new1": [{"status": "completed", "conclusion": "failure"}]}))
    assert L.load()["staged"] is None


def test_up_to_date_makes_no_downloads(L):
    st = L.load()
    st["heads"]["A"] = "new1"
    gh = GitHub()
    assert L.check(st, gh) == "Rosie is up to date."
    assert not any("codeload" in c or "check-runs" in c for c in gh.calls)


def test_a_version_that_fails_its_start_check_here_is_never_tried_again(L, monkeypatch):
    monkeypatch.setattr(L, "install_libraries", lambda slot: None)
    monkeypatch.setattr(L, "smoke", lambda slot: {"ok": False, "checks": {"starts": False}, "error": "ImportError: winreg"})
    st = L.load()
    st["heads"]["A"] = "old"
    assert "did not start cleanly" in L.check(st, GitHub())
    st = L.load()
    assert "new1" in st["bad"] and st["staged"] is None
    assert "failed here before" in L.check(st, GitHub())


def test_a_download_problem_is_tried_again_next_time(L, monkeypatch):
    def broken(url, timeout=30):
        if "codeload" in url:
            raise OSError("connection reset")
        return GitHub()(url, timeout)
    st = L.load()
    st["heads"]["A"] = "old"
    assert "Could not build" in L.check(st, broken)
    st = L.load()
    assert st["bad"] == [] and "B" not in st["heads"]


def test_a_download_cannot_write_outside_its_slot(L):
    gh = GitHub(files={"../../evil.txt": "x"})
    with pytest.raises(RuntimeError, match="unsafe path"):
        L.download("new1", L.SLOTS / ".B-new", gh)
    assert not (L.ROOT / "evil.txt").exists() and not (L.ROOT.parent / "evil.txt").exists()


def test_switch_then_rollback_goes_back_and_marks_the_version_bad(L, monkeypatch):
    for s in ("A", "B"):
        py = L.venv_python(s)
        py.parent.mkdir(parents=True)
        py.write_text("")
    st = L.load()
    st.update(heads={"A": "old", "B": "new1"}, staged="new1")
    L.switch(st)
    st = L.load()
    assert st["active"] == "B" and st["previous"] == "A" and st["staged"] is None and st["probation_until"] > time.time()
    L.rollback(st, "the new version stopped answering")
    st = L.load()
    assert st["active"] == "A" and "new1" in st["bad"] and not st["probation_until"]


def test_removing_a_slot_never_touches_the_shared_data(L):
    (L.ROOT / "data").mkdir()
    (L.ROOT / "data" / "chats.db").write_text("hers")
    (L.SLOTS / "B").mkdir()
    L.link_shared("B")
    L._remove(L.SLOTS / "B")
    assert (L.ROOT / "data" / "chats.db").read_text() == "hers"


def test_updates_go_in_only_when_the_pc_is_quiet_or_at_night(L, monkeypatch):
    noon = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))
    three = time.mktime((2026, 10, 23, 3, 0, 0, 0, 0, -1))
    monkeypatch.setattr(L, "idle_seconds", lambda: 30)
    assert L.quiet_now(noon) is False and L.quiet_now(three) is True
    monkeypatch.setattr(L, "idle_seconds", lambda: 3600)
    assert L.quiet_now(noon) is True
    monkeypatch.setattr(L, "idle_seconds", lambda: None)      # can't tell: wait for the night
    assert L.quiet_now(noon) is False


def test_the_launcher_uses_only_the_standard_library():
    import ast
    import sys
    tree = ast.parse(SRC.read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods - set(sys.stdlib_module_names)


def test_the_windows_start_files_use_windows_line_endings():
    for name in ("Rosie.bat", "Install Rosie.bat"):
        raw = (SRC.parent / name).read_bytes()
        assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"), name


def test_the_checker_uses_a_model_she_already_has_instead_of_downloading(L):
    assert L.pick_model(["nomic-embed-text:latest", "qwen3.5:9b"], ask=lambda q: pytest.fail("asked")) == "qwen3.5:9b"
    answers = iter(["7", "2"])                                     # a wrong number, then her pick
    assert L.pick_model(["llama3.1:8b", "nomic-embed-text:latest", "gemma4:12b"], ask=lambda q: next(answers)) == "gemma4:12b"
    assert L.pick_model(["llama3.1:8b"], ask=lambda q: "") is None  # Enter: download the usual one
    assert L.pick_model(["nomic-embed-text:latest"], ask=lambda q: pytest.fail("asked")) is None


def test_setup_sets_the_model_and_connects_agent_zero_when_it_runs(L, monkeypatch):
    calls = []
    monkeypatch.setattr(L, "check_pc", lambda: {"system": "Windows-11", "disk_free_gb": 200, "gpu": None, "ollama": "ollama", "models": ["gemma4:12b"],
                                                "agent_zero": {"found": True, "container": "a0", "image": "agent0ai/agent-zero", "running": True},
                                                "installed": True})
    monkeypatch.setattr(L, "pick_model", lambda models: "gemma4:12b")
    monkeypatch.setattr(L.subprocess, "run", lambda args, **kw: (calls.append((args, kw.get("env", {}).get("ROSIE_A0_CONTAINER"))), L.subprocess.CompletedProcess(args, 0))[1])
    assert L.setup() == 0
    assert json.loads((L.ROOT / "data" / "config.json").read_text())["chat_model"] == "gemma4:12b"
    (args, container), = calls
    assert args[1].endswith("configure.py") and args[2] == "gemma4:12b" and container == "a0"


def test_setup_without_agent_zero_leaves_rosie_answering_herself(L, monkeypatch):
    monkeypatch.setattr(L, "check_pc", lambda: {"system": "Windows-11", "disk_free_gb": 200, "gpu": None, "ollama": "ollama", "models": ["qwen3.5:9b"],
                                                "agent_zero": {"found": False, "why": "Docker isn't installed."}, "installed": True})
    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: pytest.fail("ran something"))
    assert L.setup() == 0
    assert json.loads((L.ROOT / "data" / "config.json").read_text()) == {"chat_model": "qwen3.5:9b"}
