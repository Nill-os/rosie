import compress


def chat(n, size=100):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question {i}\n" + "x" * size})
        out.append({"role": "assistant", "content": "a" * size})
    return out


def test_under_limit_returns_same_list():
    h = chat(3)
    assert compress.compress(h) is h


def test_old_messages_become_one_summary():
    h = chat(20, 1000)
    out = compress.compress(h, keep_recent=6, max_chars=5000)
    assert len(out) == 7
    assert out[0]["content"].startswith(compress.PREFIX)
    assert out[1:] == h[-6:]
    assert "question 0" in out[0]["content"]


def test_summarize_callable_used():
    got = []
    out = compress.compress(chat(20, 1000), 4, 5000, summarize=lambda t: got.append(t) or "SUM")
    assert out[0]["content"] == f"{compress.PREFIX} SUM" and got


def test_summarize_failure_falls_back():
    def boom(t):
        raise RuntimeError
    out = compress.compress(chat(20, 1000), 4, 5000, summarize=boom)
    assert "question 0" in out[0]["content"]


def test_tool_pair_not_split():
    h = chat(10, 1000)
    h[-3:-3] = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "find_free_time"}}]}, {"role": "tool", "content": "r" * 1000}]
    out = compress.compress(h, keep_recent=3, max_chars=2000)
    kept = out[1:]
    assert kept[0]["role"] != "tool"
    assert any(m.get("tool_calls") for m in kept) or "find_free_time" in out[0]["content"]


def test_extra_keys_preserved_and_idempotent():
    h = chat(20, 1000)
    h[-1]["extra"] = 5
    out = compress.compress(h, 4, 5000)
    assert out[-1]["extra"] == 5
    assert compress.compress(out, 4, 50000) is out


def test_fallback_lists_tools_and_caps_lines():
    h = [{"role": "user", "content": "y" * 500}, {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_search"}}]}] + chat(10, 1000)
    s = compress.fallback(h[:2])
    assert "web_search" in s and len(s.splitlines()[0]) <= 162
