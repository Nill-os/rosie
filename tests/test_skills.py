from pathlib import Path
import pytest
import agent
import skills


def pick(text):
    return agent.pick_skill(text, [])


def test_the_new_skills_are_picked_without_asking_the_model(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    monkeypatch.setattr(agent.workflows, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model should not be asked")))
    assert pick("make a new Keep note with the best pizza crust recipe") == "meal-planning"   # save-recipe merged in (2026-10-23)
    assert pick("reply to Sam's text saying sounds good") == "reply-to-text"
    assert pick("where should we eat tonight, a restaurant that works for their diet") == "meal-planning"
    assert pick("add a task to buy stamps") == ""


def test_starter_skills_are_written_and_listed(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    skills.init()
    assert "reply-to-text" in [n for n, _ in skills.listing()] and "meal-planning" in [n for n, _ in skills.listing()]


def test_audit_routing_fixes(monkeypatch, tmp_path):
    """Skill audit 2026-10-23 pass 2: running late, meal plans, recipe notes, and email replies reach the right playbook and tools."""
    monkeypatch.setattr(skills, "DIR", tmp_path)
    monkeypatch.setattr(agent.workflows, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model should not be asked")))
    assert pick("I'm running 20 minutes late") == "plan-my-day"
    assert pick("Save that recipe to my notes app") == "meal-planning"
    assert "phone" in agent.select_groups("Save that recipe to my notes app", [], "meal-planning")
    assert pick("reply to her email and say thanks") == ""                     # mail tools, not the texting playbook
    assert "phone" in agent.select_groups("write back to mom", [], "reply-to-text")
    assert agent.call_problem("shift_day", {}, "I am running late, push everything back 20 min", "") == ""
    assert "no tool" in agent.call_problem("shift_day", {}, "push everything to tomorrow", "")


def test_unedited_old_defaults_are_replaced_and_save_recipe_retires(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setattr(skills, "DIR", tmp_path)
    old = "Use when the user wants a recipe saved.\n\nold steps\n"
    (tmp_path / "save-recipe.md").write_text(old)
    (tmp_path / "plan-my-day.md").write_text("my own edited plan\n")
    monkeypatch.setitem(skills.OLD_DEFAULTS, "save-recipe", {hashlib.sha256(old.encode()).hexdigest()[:16]})
    skills.init()
    assert not (tmp_path / "save-recipe.md").exists()
    assert (tmp_path / "plan-my-day.md").read_text() == "my own edited plan\n"              # the owner's own edit stays
    (tmp_path / "keep.md").write_text("x")
    (tmp_path / "save-recipe.md").write_text("my own recipe playbook\n")
    skills.init()
    assert (tmp_path / "save-recipe.md").exists()                                         # an edited one is never deleted


def test_shift_day_never_pushes_events_into_another_day(monkeypatch):
    """Skill audit pass 3: "push everything back a day" must not move the day's blocks to tomorrow."""
    import datetime as dt
    import actions
    import gcal
    day = dt.date(2026, 10, 23)
    evs = [{"id": "1", "title": "Work block", "start": f"{day}T13:00:00-07:00", "end": f"{day}T14:00:00-07:00", "all_day": False}]
    monkeypatch.setattr(gcal, "events_on", lambda d, cal=None: evs)
    monkeypatch.setattr(gcal, "write_calendar_id", lambda: "test")
    monkeypatch.setattr(gcal, "move_event", lambda *a, **k: pytest.fail("moved an event"))
    assert "another day" in actions.shift_day(1440, from_time=f"{day}T00:00:00-07:00", day=str(day))["message"]


def test_email_replies_and_text_backs_route_right(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    monkeypatch.setattr(agent.workflows, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model should not be asked")))
    assert pick("reply to that email about lunch") == ""
    assert pick("can you text her back that dinner is at 7") == "reply-to-text"
    assert "phone" in agent.select_groups("save this recipe", [], "meal-planning")


@pytest.mark.parametrize("now,late,block,ok", [
    ("21:00", 20, ("22:45", "23:45"), True),          # the last block now ends at 00:05: still today's shift
    ("20:00", 15, ("23:00", "00:00"), True),          # a block that ends at midnight
    ("09:00", 10, ("23:00", "01:00"), True),          # a late block that already crosses midnight
    ("09:00", 1440, ("13:00", "14:00"), False),       # "push everything back a day"
    ("22:00", 150, ("23:00", "23:30"), False),        # starts tomorrow after the shift
])
def test_shift_day_keeps_late_evening_shifts_and_refuses_moves_into_another_day(monkeypatch, now, late, block, ok):
    """Skill audit passes 3 and 4."""
    import datetime as dt
    import actions
    import gcal
    day = dt.date(2026, 10, 23)
    start = dt.datetime.combine(day, dt.time.fromisoformat(block[0]), tzinfo=gcal.TZ)
    end = dt.datetime.combine(day, dt.time.fromisoformat(block[1]), tzinfo=gcal.TZ)
    end += dt.timedelta(days=1) if end <= start else dt.timedelta()
    evs = [{"id": "1", "title": "Work block", "start": start.isoformat(), "end": end.isoformat(), "all_day": False}]
    moved = []
    monkeypatch.setattr(gcal, "events_on", lambda d, cal=None: evs)
    monkeypatch.setattr(gcal, "write_calendar_id", lambda: "test")
    monkeypatch.setattr(gcal, "move_event", lambda *a, **k: moved.append(a))
    cut = dt.datetime.combine(day, dt.time.fromisoformat(now), tzinfo=gcal.TZ).isoformat()
    out = actions.shift_day(late, from_time=cut, day=str(day))
    assert (out.get("status") == "done" and moved) if ok else ("another day" in out.get("message", "") and not moved)


def test_same_day_words_and_coding_requests_route_right(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    monkeypatch.setattr(agent.workflows, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    assert agent.call_problem("shift_day", {}, "push everything back 30 minutes, I have to get to the holiday party", "") == ""
    assert agent.call_problem("shift_day", {}, "push everything back an hour so I can get to today's class", "") == ""
    assert "no tool" in agent.call_problem("shift_day", {}, "push everything to next week", "")
    assert agent.call_problem("shift_day", {}, "push everything back an hour, I'm off next week", "") == ""
    assert pick("reply to the email message from HR about lunch") == ""
    assert pick("reply to her text and then check my email") == "reply-to-text"
