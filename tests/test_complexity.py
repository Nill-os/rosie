import complexity


def test_greetings_are_trivial():
    for t in ("hi", "Hey Rosie!", "thanks", "good morning", ""):
        assert complexity.score(t)["tier"] == "trivial"


def test_simple_question_is_not_complex():
    assert complexity.score("what time is it in Tokyo?")["tier"] in ("trivial", "simple")


def test_multi_step_analysis_is_complex():
    r = complexity.score("First compare the two plans and explain why one is better, then write a python script to do it. What are the trade-offs? Which is cheaper?")
    assert r["tier"] == "complex"
    assert r["max_steps"] == 10 and r["num_predict"] == 2000


def test_voice_stays_short():
    r = complexity.score("First compare the two plans and explain why, then write a python script. Why? How?", voice=True)
    assert r["num_predict"] <= 350
    assert complexity.score("hi", voice=True)["num_predict"] == 300


def test_long_history_raises_score():
    h = [{"role": "user", "content": "x"}] * 25
    assert complexity.score("tell me more", h)["score"] > complexity.score("tell me more")["score"]


def test_score_bounded_and_tiers_ordered():
    r = complexity.score("explain " * 500 + "? ? ? ?", [{}] * 30)
    assert 0 <= r["score"] <= 1
    steps = [complexity.score(t)["max_steps"] for t in ("hi", "what is the weather today", "explain why the sky is blue and compare it to sunsets", "first plan then do 1. a 2. b explain why? how? ```code```")]
    assert steps == sorted(steps)
