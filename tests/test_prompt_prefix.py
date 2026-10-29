import datetime as dt
import os

import agent
import memory
import mind


def common_prefix_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def build(monkeypatch, when, mem, about="", work=""):
    monkeypatch.setattr(agent, "now", lambda: when)
    monkeypatch.setattr(memory, "core_block", lambda: mem)
    monkeypatch.setattr(mind, "about_owner", lambda: about)
    monkeypatch.setattr(agent.workflows, "memfile_block", lambda: work)
    return agent.system_prompt("auto", False)


def measure(monkeypatch):
    tz = agent.gcal.TZ
    a = build(monkeypatch, dt.datetime(2026, 10, 28, 9, 0, tzinfo=tz), "likes tea", "owner a", "focus x")
    b = build(monkeypatch, dt.datetime(2026, 11, 3, 17, 45, tzinfo=tz), "likes coffee", "owner b", "focus y")
    n = common_prefix_len(a.encode(), b.encode())
    return n, len(a.encode()), len(b.encode())


def test_volatile_parts_come_after_a_long_stable_prefix(monkeypatch):
    n, la, lb = measure(monkeypatch)
    print(f"stable prefix {n} bytes of {la}/{lb}")
    assert n > 0.6 * min(la, lb)


def test_stable_prefix_ends_at_the_now_line(monkeypatch):
    tz = agent.gcal.TZ
    a = build(monkeypatch, dt.datetime(2026, 10, 28, 9, 0, tzinfo=tz), "m")
    b = build(monkeypatch, dt.datetime(2026, 10, 29, 9, 0, tzinfo=tz), "m")
    n = common_prefix_len(a.encode(), b.encode())
    assert a.encode()[:n].decode(errors="ignore").rstrip().endswith("NOW:") or "NOW:" in a[len(a[:n]) - 20:len(a[:n]) + 20]


def test_tool_array_order_is_stable_across_group_sets():
    one = [t["function"]["name"] for t in agent._schema({"core", "calendar", "web"})]
    two = [t["function"]["name"] for t in agent._schema({"web", "calendar", "core"})]
    assert one == two
