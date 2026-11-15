import httpx

import agent


class BadBody:
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class ListBody:
    def json(self):
        return ["not", "a", "dict"]


def turn():
    return agent.Turn([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], [], "hi", set(), {"num_predict": 64}, False)


def test_non_json_reply_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: BadBody())
    m, err = agent._call_model(turn())
    assert m is None and "not answering" in err[0] and err[1] == []
    print("PASS: bad json handled")


def test_non_object_reply_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: ListBody())
    m, err = agent._call_model(turn())
    assert m is None and "did not answer" in err[0]
    print("PASS: non-object reply handled")
