import datetime as dt

import pytest

import inbox
import proposals

T0 = dt.datetime(2026, 10, 1, 8, 0)


@pytest.fixture
def clock(monkeypatch):
    box = {"t": T0}
    monkeypatch.setattr(proposals, "_now", lambda: box["t"])
    return box


def decline(key, kind="task", n=0):
    pid = proposals.add(kind, f"t{key}{n}", {"title": "x"}, key=key)
    assert pid
    proposals.dismiss(pid)
    return pid


def test_declined_key_is_suppressed_then_returns_after_escalating_cooldown(clock):
    key = "k1"
    decline(key)
    assert proposals.known(key) and proposals.add("task", "again", {}, key=key) is None
    clock["t"] = T0 + dt.timedelta(hours=23)
    assert proposals.add("task", "again", {}, key=key) is None
    clock["t"] = T0 + dt.timedelta(days=1, minutes=1)
    assert not proposals.known(key)
    pid2 = proposals.add("task", "again", {"title": "x"}, key=key)
    assert pid2
    proposals.dismiss(pid2)
    clock["t"] += dt.timedelta(days=2)
    assert proposals.add("task", "later", {}, key=key) is None
    clock["t"] += dt.timedelta(days=1, minutes=1)
    pid3 = proposals.add("task", "later", {"title": "x"}, key=key)
    assert pid3
    proposals.dismiss(pid3)
    clock["t"] += dt.timedelta(days=6)
    assert proposals.known(key)
    clock["t"] += dt.timedelta(days=1, minutes=1)
    pid4 = proposals.add("task", "x", {"title": "x"}, key=key)
    proposals.dismiss(pid4)
    clock["t"] += dt.timedelta(days=29)
    assert proposals.known(key)
    clock["t"] += dt.timedelta(days=2)
    assert proposals.add("task", "x", {"title": "x"}, key=key)
    with proposals._db() as c:
        assert c.execute("SELECT declines FROM nudge_key WHERE key=?", (key,)).fetchone()["declines"] == 4


def test_system_dismissal_is_not_counted_and_stays_suppressed(clock):
    pid = proposals.add("market_term", "t", {}, key="sys")
    proposals.dismiss(pid, declined=False)
    clock["t"] += dt.timedelta(days=400)
    assert proposals.known("sys") and proposals.kind_stats("market_term")["declined"] == 0


def test_pending_and_approved_keys_stay_blocked(clock):
    pid = proposals.add("task", "a", {"title": "a"}, key="p")
    clock["t"] += dt.timedelta(days=100)
    assert proposals.known("p") and proposals.add("task", "a", {"title": "a"}, key="p") is None
    assert proposals.approve(pid)["status"] == "approved"
    assert proposals.known("p")
    assert proposals.kind_stats("task") == {"kind": "task", "accepted": 1, "declined": 0, "rate": 1.0}


def test_low_acceptance_doubles_cooldown_and_throttles(clock):
    for i in range(6):
        decline(f"d{i}", "plan", i)
    assert proposals.kind_stats("plan")["rate"] == 0.0
    assert proposals.cooldown_days("plan", 1) == 2
    assert proposals.adjusted_limit("plan", 3) == 1
    assert proposals.add("plan", "p", {}, key="n1") is None
    clock["t"] += dt.timedelta(hours=25)
    assert proposals.add("plan", "p", {}, key="n1")
    assert proposals.add("plan", "q", {}, key="n2") is None
    assert proposals.add("event", "other kind", {}, key="n3")


def test_high_acceptance_shortens_cooldown_and_raises_limit(clock):
    for i in range(6):
        pid = proposals.add("task", f"a{i}", {"title": f"a{i}"}, key=f"a{i}")
        assert proposals.approve(pid)["status"] == "approved"
    assert proposals.cooldown_days("task", 2) == 1
    assert proposals.cooldown_days("task", 4) == 15
    assert proposals.adjusted_limit("task", 3) == 4


def test_few_decisions_do_not_change_behavior(clock):
    decline("f1")
    assert proposals.cooldown_days("task", 1) == 1 and proposals.adjusted_limit("task", 3) == 3
