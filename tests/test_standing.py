import datetime as dt

import pytest

import automations
import inbox
import standing

T0 = dt.datetime(2026, 10, 28, 9, 0).astimezone()


def test_add_list_remove():
    r = standing.add("Ask about the invoice", "invoice, receipt", "mail")
    assert r["status"] == "standing reminder set"
    assert standing.add("Ask about the invoice", ["invoice", "receipt"], "mail")["status"] == "already set"
    got = standing.list_()
    assert got[0]["keywords"] == ["invoice", "receipt"] and got[0]["source"] == "mail"
    assert standing.api_list()["standing"][0]["id"] == r["id"]
    assert standing.api_remove({"id": r["id"]})["status"] == "removed"
    assert standing.remove(r["id"])["error"]
    assert standing.list_() == []


def test_add_rejects_bad_input():
    assert standing.add("", "x")["error"]
    assert standing.add("hi", "")["error"]
    assert standing.add("hi", "x", "carrier pigeon")["error"]


def test_match_uses_whole_words_and_source():
    standing.add("Call the bank", "bank", "mail")
    assert standing.match("Your Bank statement is ready", "mail", T0)
    assert not standing.match("A riverbank walk", "mail", T0 + dt.timedelta(days=1))
    assert not standing.match("bank news", "sms", T0 + dt.timedelta(days=2))
    assert standing.match("bank news", "any", T0 + dt.timedelta(days=3))


def test_cooldown_and_daily_budget():
    standing.add("Water plants", "plants", "any", cooldown_min=30, per_day=2)
    assert len(standing.match("the plants", "inbox", T0)) == 1
    assert standing.match("the plants", "inbox", T0 + dt.timedelta(minutes=10)) == []
    assert len(standing.match("the plants", "inbox", T0 + dt.timedelta(minutes=40))) == 1
    assert standing.match("the plants", "inbox", T0 + dt.timedelta(hours=3)) == []
    assert len(standing.match("the plants", "inbox", T0 + dt.timedelta(days=1, hours=3))) == 1


def test_tool_actions():
    assert standing.tool("add", message="Buy stamps", keywords="post office")["id"]
    assert standing.tool("list")["standing"][0]["keywords"] == ["post office"]
    assert standing.tool("remove", id=standing.list_()[0]["id"])["status"] == "removed"
    assert standing.tool("nope")["error"]


def test_check_event_adds_one_inbox_item():
    standing.add("Ask about the invoice", "invoice", "mail")
    fired = standing.check_event("mail", "Re: invoice 42 attached", T0)
    assert len(fired) == 1
    items = [i for i in inbox.list_items() if i["kind"] == "standing"]
    assert len(items) == 1 and "Ask about the invoice" in items[0]["title"]


def test_job_standing_scans_only_new_inbox_items(monkeypatch):
    standing.add("Ask about the invoice", "invoice", "any", cooldown_min=0)
    monkeypatch.setattr(automations, "_standing", {"seen": None})
    inbox.add("mail", "old invoice", "", desktop=False)
    automations.job_standing(T0, {})
    assert not [i for i in inbox.list_items() if i["kind"] == "standing"]
    inbox.add("mail", "new invoice from Bob", "", desktop=False)
    automations.job_standing(T0, {})
    automations.job_standing(T0, {})
    assert len([i for i in inbox.list_items() if i["kind"] == "standing"]) == 1
