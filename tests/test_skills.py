import agent
import skills


def pick(text):
    return agent.pick_skill(text, [])


def test_the_new_skills_are_picked_without_asking_the_model(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    monkeypatch.setattr(agent.workflows, "llm_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model should not be asked")))
    assert pick("make a new Keep note with the best pizza crust recipe") == "save-recipe"
    assert pick("reply to Sam's text saying sounds good") == "reply-to-text"
    assert pick("where should we eat tonight, a restaurant that works for their diet") == "meal-planning"
    assert pick("add a task to buy stamps") == ""


def test_starter_skills_are_written_and_listed(monkeypatch, tmp_path):
    monkeypatch.setattr(skills, "DIR", tmp_path)
    skills.init()
    assert "reply-to-text" in [n for n, _ in skills.listing()] and "meal-planning" in [n for n, _ in skills.listing()]
