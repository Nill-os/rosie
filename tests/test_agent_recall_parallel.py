import threading

import agent
import traces

TEXT = "what did I read about quantum gardening robots"


def fakes(monkeypatch, graph="G", mind="M", board="B"):
    monkeypatch.setattr(agent.corner_graph, "recall", lambda t: graph() if callable(graph) else graph)
    monkeypatch.setattr(agent.mind, "auto_context", lambda t: mind() if callable(mind) else mind)
    monkeypatch.setattr(agent.agentboard, "for_turn", lambda t, a: board() if callable(board) else board)


def sequential(text, given, name):
    chit = bool(agent.CHITCHAT.match(text.strip()))
    reading = given if given is not None else ("" if chit else agent.corner_graph.recall(text))
    remembered = "" if chit else agent.mind.auto_context(text)
    board = "" if given is not None else agent.agentboard.for_turn(text, name)
    return reading, remembered, board


def test_result_matches_the_sequential_order_and_text(monkeypatch):
    fakes(monkeypatch, graph="graph text\n- a", mind="[What you remember]\n- b\n", board="board text")
    for text, given in ((TEXT, None), (TEXT, ""), (TEXT, "given"), ("thanks", None), ("thanks", "x")):
        assert agent._recall_steps(text, given, "rosie") == sequential(text, given, "rosie")
    print("PASS: parallel recall returns the same three texts as the sequential run")


def test_the_three_steps_overlap(monkeypatch):
    barrier = threading.Barrier(3, timeout=20)
    seen = []

    def step(name):
        def run():
            seen.append(name)
            barrier.wait()
            return name
        return run

    fakes(monkeypatch, graph=step("g"), mind=step("m"), board=step("b"))
    assert agent._recall_steps(TEXT, None, "rosie") == ("g", "m", "b")
    assert sorted(seen) == ["b", "g", "m"]
    print("PASS: all three steps were waiting at the same barrier at once")


def test_a_raising_step_leaves_the_others(monkeypatch):
    def boom():
        raise RuntimeError("down")

    for broken in ("graph", "mind", "board"):
        kw = {"graph": "G", "mind": "M", "board": "B"}
        kw[broken] = boom
        fakes(monkeypatch, **kw)
        got = agent._recall_steps(TEXT, None, "rosie")
        want = {"graph": "G", "mind": "M", "board": "B"}
        want[broken] = ""
        assert got == (want["graph"], want["mind"], want["board"])
    print("PASS: a step that raises gives an empty text and the other two are kept")


def test_a_slow_graph_step_that_times_out_leaves_the_others(monkeypatch):
    import corner_graph

    def slow(*a, **k):
        raise corner_graph.Slow("took longer than 4 s")

    monkeypatch.setattr(agent.corner_graph, "recall", slow)
    monkeypatch.setattr(agent.mind, "auto_context", lambda t: "M")
    monkeypatch.setattr(agent.agentboard, "for_turn", lambda t, a: "B")
    assert agent._recall_steps(TEXT, None, "rosie") == ("", "M", "B")
    print("PASS: a graph timeout leaves the memory and board texts intact")


def test_the_trace_gets_a_timing_step(monkeypatch):
    fakes(monkeypatch)
    traces.default.start(1, TEXT)
    try:
        agent._recall_steps(TEXT, None, "rosie")
        steps = [s for s in traces.default._cur["steps"] if s["kind"] == "recall"]
    finally:
        traces.default.end("")
    assert len(steps) == 1
    assert {"wall_ms", "graph_ms", "mind_ms", "board_ms"} <= set(steps[0])
    print("PASS: the turn trace records a recall step with per-step milliseconds")


def test_chat_builds_the_same_prompt_inputs(monkeypatch):
    fakes(monkeypatch, graph="graph text", mind="mind text", board="board text")
    got = {}
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")
    monkeypatch.setattr(agent.claude_plan, "wants", lambda *a, **k: False)
    monkeypatch.setattr(agent.a0, "enabled", lambda: False)
    monkeypatch.setattr(agent, "_build_turn", lambda *a, **k: got.update(args=a) or object())
    monkeypatch.setattr(agent, "_run_turn", lambda t, n: ("ok", [], []))
    agent._chat([], TEXT)
    args = got["args"]
    assert args[6] == "mind text"
    assert args[7] == "graph text\n\nboard text"
    print("PASS: the chat passes the memory text and the graph-plus-board text to the turn")
