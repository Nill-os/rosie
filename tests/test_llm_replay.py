import json

import httpx
import pytest

import llm_replay as lr

BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def test_key_ignores_key_order_and_input_type():
    assert lr.request_key({"b": 1, "a": 2}) == lr.request_key(json.dumps({"a": 2, "b": 1}).encode())
    assert lr.request_key(BODY) != lr.request_key({**BODY, "model": "n"})


def test_record_then_replay_through_a_mock_transport(tmp_path):
    path = tmp_path / "fx.json"
    calls = []

    def live(body):
        calls.append(body)
        return {"message": {"content": "hello"}}

    rec = lr.Replay(path, "record", live)
    assert rec.lookup(BODY)["message"]["content"] == "hello"
    assert rec.lookup(BODY)["message"]["content"] == "hello" and len(calls) == 1

    rep = lr.Replay(path)
    with httpx.Client(transport=rep.transport()) as c:
        r = c.post("http://x/api/chat", json=BODY)
    assert r.json() == {"message": {"content": "hello"}}
    with pytest.raises(KeyError):
        rep.lookup({**BODY, "model": "other"})


def test_a_committed_fixture_replays_a_chat_answer():
    from pathlib import Path
    rep = lr.Replay(Path(__file__).parent / "fixtures" / "llm_example.json")
    body = {"model": "example", "stream": False, "messages": [{"role": "user", "content": "Extract the city: I flew to Chicago."}]}
    with httpx.Client(transport=rep.transport()) as c:
        got = c.post("http://ollama/api/chat", json=body).json()
    assert json.loads(got["message"]["content"]) == {"city": "Chicago"}
