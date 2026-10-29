import threading
import time

import agent
import complexity


class FakeResp:
    def __init__(self, msg):
        self.msg = msg

    def json(self):
        return {"message": self.msg}


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def setup(monkeypatch, replies, seen=None):
    it = iter(replies)
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")

    def post(url, **k):
        if seen is not None:
            seen.append(k["json"])
        return FakeResp(next(it))

    monkeypatch.setattr(agent.httpx, "post", post)


def test_deadline_runs_one_finishing_pass_without_tools(fake_phone, monkeypatch):
    seen = []
    setup(monkeypatch, [call("get_weather", place="Tokyo"), {"role": "assistant", "content": "Best guess from what I found."}], seen)
    clock = iter([0.0, 0.0, 0.0, 1000.0, 1000.0, 1000.0, 1000.0])
    last = [0.0]

    real = time.monotonic
    owner = threading.get_ident()

    def mono():
        if threading.get_ident() != owner:
            return real()
        last[0] = next(clock, last[0])
        return last[0]

    monkeypatch.setattr(agent.time, "monotonic", mono)
    monkeypatch.setattr(agent.config, "load", lambda: {"turn_deadline_s": 100})
    monkeypatch.setattr(agent, "_run_call", lambda t, c: t.msgs.append({"role": "tool", "tool_name": "x", "content": "{}"}))
    reply, _, new = agent.chat([], "weather in Tokyo and then plan my trip", "auto", False)
    assert reply == "Best guess from what I found."
    assert len(seen) == 2 and "tools" not in seen[1]
    assert seen[1]["messages"][-1]["content"] == agent.FINISH_PROMPT
    assert all("Time or steps" not in str(m.get("content")) for m in new)


def test_step_cap_runs_finishing_pass(fake_phone, monkeypatch):
    seen = []
    setup(monkeypatch, [call("get_weather", place="A")] * 2 + [{"role": "assistant", "content": "Here is what I have."}], seen)
    monkeypatch.setattr(agent, "_run_call", lambda t, c: t.msgs.append({"role": "tool", "tool_name": "x", "content": "{}"}))
    reply, _, _ = agent.chat([], "weather please", "auto", False, max_steps=2)
    assert reply == "Here is what I have." and len(seen) == 3


def test_stuck_only_if_finishing_pass_fails(fake_phone, monkeypatch):
    setup(monkeypatch, [call("get_weather", place="A"), {"role": "assistant", "content": ""}])
    monkeypatch.setattr(agent, "_run_call", lambda t, c: None)
    reply, _, _ = agent.chat([], "weather please", "auto", False, max_steps=1)
    assert reply == "I got stuck. Please rephrase."


def test_exactly_one_terminal_result_per_turn(fake_phone, monkeypatch):
    seen = []
    setup(monkeypatch, [call("get_weather", place="A"), {"role": "assistant", "content": "Done once."}], seen)
    monkeypatch.setattr(agent, "_run_call", lambda t, c: None)
    t = agent._build_turn([], "weather", "auto", False, None, "", "", "", False, complexity.score("weather"), None)
    first = agent._run_turn(t, 1)
    second = agent._run_turn(t, 1)
    assert first is second and len(seen) == 2


def test_queued_message_is_injected_at_next_step(fake_phone, monkeypatch):
    seen = []
    setup(monkeypatch, [call("list_tasks"), {"role": "assistant", "content": "Handled both."}], seen)
    agent.begin(7)
    monkeypatch.setattr(agent, "_run_call", lambda t, c: agent.queue_message(7, "also check Paris"))
    reply, _, new = agent.chat([], "hello there friend", "auto", False, chat_id=7)
    assert reply == "Handled both."
    assert "also check Paris" in str(seen[1]["messages"][-1]["content"])
    assert any("also check Paris" in str(m.get("content")) for m in new)
    assert agent.end(7) == []


def test_queue_is_isolated_per_chat():
    agent.begin(1)
    agent.begin(2)
    assert agent.queue_message(1, "for one")
    assert agent.drain(2) == []
    assert agent.drain(1) == ["for one"]
    assert agent.end(1) == [] and agent.end(2) == []
    assert not agent.queue_message(1, "nobody home")
    assert not agent.running(1)
