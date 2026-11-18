import re
from pathlib import Path

from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parent.parent / "app"


def test_no_app_file_uses_the_deprecated_startup_hook():
    hits = [p.name for p in sorted(APP.glob("*.py")) if re.search(r"\bon_event\s*\(", p.read_text())]
    assert hits == []
    print("PASS: no file under app/ registers a start-up step with the deprecated on_event hook")


def test_the_server_runs_its_startup_steps_once_when_it_starts(monkeypatch):
    import server
    calls = []
    monkeypatch.setattr(server, "_startup", lambda: calls.append("start"))
    with TestClient(server.app):
        assert calls == ["start"]
    assert calls == ["start"]
    print("PASS: the server runs its start-up steps exactly once, when the app starts")


def test_the_phone_pod_warms_up_once_when_it_starts(monkeypatch):
    import phone_pod
    calls = []
    monkeypatch.setattr(phone_pod, "warm", lambda: calls.append("warm"))
    with TestClient(phone_pod.app):
        assert calls == ["warm"]
    assert calls == ["warm"]
    print("PASS: the phone pod warms up exactly once, when the app starts")


def test_a_failing_startup_step_does_not_stop_the_server(monkeypatch, capsys):
    import server
    import automations
    monkeypatch.delenv("ROSIE_SANDBOX", raising=False)
    names = []
    real = server._startup

    def only_failing():
        import power, models, ctxsize, skill_sync, vector_pod
        for mod, attr in ((power, "clear_flag"), (models, "load_saved"), (ctxsize, "push"), (automations, "start"),
                          (skill_sync, "start"), (vector_pod, "start")):
            monkeypatch.setattr(mod, attr, lambda a=attr: (names.append(a), (_ for _ in ()).throw(RuntimeError("no")))[1])
        monkeypatch.setattr(server, "_warm_up", lambda: None)
        for mod in ("a0_mcp", "a0_proxy", "board_link"):
            m = __import__(mod)
            monkeypatch.setattr(m, "start", lambda: (_ for _ in ()).throw(RuntimeError("no")))
        import a0, a0_host, claude_plan
        monkeypatch.setattr(a0_host, "detect", lambda: (_ for _ in ()).throw(RuntimeError("no")))
        monkeypatch.setattr(a0, "relocate", lambda: (_ for _ in ()).throw(RuntimeError("no")))
        monkeypatch.setattr(claude_plan, "validate_free_keys", lambda: None)
        real()

    monkeypatch.setattr(server, "_startup", only_failing)
    with TestClient(server.app) as c:
        assert c.get("/definitely-not-a-page").status_code in (401, 403, 404)
    out = capsys.readouterr().out
    assert sorted(names) == ["clear_flag", "load_saved", "push", "start", "start", "start"] and out.count("not started") >= 9
    print("PASS: every failing start-up step is reported and the server still starts and answers")
