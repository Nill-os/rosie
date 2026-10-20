import agent
import lessons


def test_seed_lessons_exist_once_and_stay_deleted():
    first = lessons.list_all()
    assert len(first) >= 5
    for r in first:
        lessons.delete(r["id"])
    assert lessons.list_all() == []                      # deleting them all stays deleted


def test_relevant_lessons_are_few_and_on_topic():
    got = lessons.relevant("do the insulation on Tuesday for a full day")
    assert 1 <= len(got) <= 2
    assert lessons.relevant("tell me a joke") == []
    assert "LESSONS" in lessons.block("push everything on Tuesday forward")


def test_a_correction_is_saved_and_shows_up_next_time():
    lessons.add("pizza crust recipe keep", "Always include the source link in a recipe note.")
    assert any("source link" in l for l in lessons.relevant("save the pizza crust recipe to keep"))
    assert "source link" in agent.lessons.block("save a pizza crust recipe in keep")


def test_the_lessons_tool_lists_adds_and_deletes():
    out = lessons.tool("add", "coffee order mornings", "Coffee is oat milk.")
    assert out["status"] == "saved"
    assert any(l["lesson"].startswith("Coffee") for l in lessons.tool("list")["lessons"])
    assert lessons.tool("delete", lesson_id=out["id"])["status"] == "deleted"
    assert "lessons" in agent.TOOLS
