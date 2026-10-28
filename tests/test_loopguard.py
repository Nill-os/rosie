import loopguard
from loopguard import LoopGuard


def test_identical_call_blocked_after_limit():
    g = LoopGuard()
    assert g.check("search", {"q": "x"}) is None
    assert g.check("search", {"q": "x"}) is None
    msg = g.check("search", {"q": "x"})
    assert msg and "search" in msg and "2 times" in msg
    assert g.check("search", {"q": "x"})


def test_different_args_not_blocked():
    g = LoopGuard()
    for i in range(10):
        assert g.check("search", {"q": i}) is None


def test_key_order_independent():
    g = LoopGuard()
    assert g.check("t", {"a": 1, "b": 2}) is None
    assert g.check("t", {"b": 2, "a": 1}) is None
    assert g.check("t", {"a": 1, "b": 2})


def test_unserializable_args_do_not_crash():
    g = LoopGuard()
    assert g.check("t", {"a": object}) is None


def test_ping_pong_abab():
    g = LoopGuard()
    assert g.check("a", {}) is None
    assert g.check("b", {}) is None
    assert g.check("a", {}) is None
    assert g.check("b", {})


def test_ping_pong_abcabc():
    g = LoopGuard()
    for n in ("a", "b", "c", "a", "b"):
        assert g.check(n, {}) is None
    assert g.check("c", {})


def test_abc_then_new_call_is_fine():
    g = LoopGuard()
    for n in ("a", "b", "c", "a", "b"):
        assert g.check(n, {}) is None
    assert g.check("d", {}) is None


def test_ping_pong_needs_same_args():
    g = LoopGuard()
    assert g.check("a", {"i": 1}) is None
    assert g.check("b", {}) is None
    assert g.check("a", {"i": 2}) is None
    assert g.check("b", {"i": 2}) is None


def test_ping_pong_outside_window_ignored():
    g = LoopGuard(window=2)
    assert g.check("a", {}) is None
    assert g.check("b", {}) is None
    assert g.check("a", {}) is None
    assert g.check("b", {}) is None


def test_blocked_call_is_not_recorded():
    g = LoopGuard()
    g.check("x", {})
    g.check("x", {})
    assert g.check("x", {})
    assert g.check("x", {})
    assert g.history == [("x", "{}")] * 2
    assert g.seen[("x", "{}")] == 2
    assert g.check("y", {}) is None


def test_poll_budget():
    g = LoopGuard()
    for i in range(6):
        assert g.check("board", {"n": i}) is None
    msg = g.check("board", {"n": 99})
    assert msg and "board" in msg
    assert g.check("other", {}) is None


def test_poll_budget_only_for_poll_tools():
    g = LoopGuard()
    assert "board" in loopguard.POLL_TOOLS
    for i in range(20):
        assert g.check("search", {"n": i}) is None


def test_instances_independent():
    a, b = LoopGuard(), LoopGuard()
    a.check("t", {})
    a.check("t", {})
    assert a.check("t", {})
    assert b.check("t", {}) is None
