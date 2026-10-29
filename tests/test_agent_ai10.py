import agent
import compress
import test_agent_deadline as d


def test_arg_feedback_names_missing_and_signature():
    msg = agent.arg_feedback("move_event", {"event_id": "x"})
    assert "missing required: new_start, new_end" in msg and "move_event(event_id: string" in msg


def test_arg_feedback_names_wrong_type():
    assert "minutes_late must be integer" in agent.arg_feedback("shift_day", {"minutes_late": "soon"})
    assert agent.arg_feedback("shift_day", {"minutes_late": "15"}) == ""


def test_arg_feedback_ok_and_unknown_tool():
    assert agent.arg_feedback("add_task", {"title": "x"}) == ""
    assert agent.arg_feedback("nope", {}) == ""


def test_execute_returns_feedback_instead_of_exception():
    res, _ = agent._execute("shift_day", {}, None)
    assert "Bad arguments for shift_day" in res["error"] and "TypeError" not in res["error"]


def test_rank_groups_uses_descriptions():
    assert "mail" in agent.rank_groups("anything from my gmail sender about unsubscribe")
    assert agent.rank_groups("") == [] and agent.rank_groups("zzzqqq") == []
    assert len(agent.rank_groups("calendar event meeting", 2)) <= 2


def test_select_groups_falls_back_to_calendar_web_when_nothing_scores():
    assert agent.select_groups("zzzqqq", []) == {"core", "calendar", "web"}


def test_select_groups_ranker_adds_group():
    g = agent.select_groups("tell me about a lifequest xp", [])
    assert "lifequest" in g


def test_prune_replaces_old_big_tool_results_only():
    h = [{"role": "user", "content": "q"}]
    for i in range(5):
        h += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "t"}}]}, {"role": "tool", "content": f"r{i}\n" + "x" * 3000}]
    out = compress.prune(h)
    tools = [m["content"] for m in out if m["role"] == "tool"]
    assert tools[0].startswith("[old tool result trimmed") and tools[1].startswith("[old tool result trimmed")
    assert all(len(c) > 3000 for c in tools[-3:])
    assert compress.prune(out) is out


def test_compress_prunes_before_summarizing():
    h = [{"role": "user", "content": "q"}]
    for i in range(6):
        h += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "t"}}]}, {"role": "tool", "content": "x" * 3000}]
    out = compress.compress(h, keep_recent=4, max_chars=11000, summarize=lambda t: "SUM")
    assert not out[0]["content"].startswith(compress.PREFIX)
    assert len(out) == len(h)


def test_budget_note_injected_once_near_cap(monkeypatch):
    seen = []
    replies = [d.call("what_now") for _ in range(6)] + [{"role": "assistant", "content": "done"}]
    d.setup(monkeypatch, replies, seen)
    agent.chat([], "what is next", max_steps=4)
    notes = [m for s in seen for m in s["messages"] if "Budget notice" in str(m.get("content"))]
    assert notes and "2 steps left" in notes[0]["content"]
    last_msgs = [m["content"] for m in seen[-1]["messages"] if "Budget notice" in str(m.get("content"))]
    assert len(last_msgs) == 1
