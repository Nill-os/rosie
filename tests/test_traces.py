import json

import traces


def point(monkeypatch, tmp_path):
    p = tmp_path / "traces.jsonl"
    monkeypatch.setattr(traces, "path", lambda: p)
    return p


def test_turn_recorded_and_written(monkeypatch, tmp_path):
    p = point(monkeypatch, tmp_path)
    t = traces.Trace()
    t.start(1, "hello")
    t.step("tool", name="x")
    t.step("blocked", name="y")
    t.end("hi")
    row = json.loads(p.read_text().splitlines()[0])
    assert row["turn_id"] == 1 and len(row["steps"]) == 2 and row["reply"] == "hi"
    assert t.summary() == {"turns": 1, "avg_steps": 2.0, "blocked": 1, "loops": 0}


def test_keeps_last_200(monkeypatch, tmp_path):
    point(monkeypatch, tmp_path)
    t = traces.Trace()
    for i in range(250):
        t.start(i, "q")
        t.end("a")
    assert len(t.recent(1000)) == 200
    assert t.recent(2)[-1]["turn_id"] == 249


def test_rotates_at_cap(monkeypatch, tmp_path):
    p = point(monkeypatch, tmp_path)
    monkeypatch.setattr(traces, "MAX_BYTES", 100)
    t = traces.Trace()
    for i in range(5):
        t.start(i, "q" * 50)
        t.end("a")
    assert p.with_name("traces.jsonl.1").exists()


def test_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(traces, "path", lambda: tmp_path / "file" / "x" / "t.jsonl")
    (tmp_path / "file").write_text("block")
    t = traces.Trace()
    t.step("tool")
    t.end("none")
    t.start(1, "q")
    t.step("loop")
    t.end("r")
    assert t.summary()["loops"] == 1
