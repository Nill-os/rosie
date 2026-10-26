import time

import pytest

import vector_intents
import vector_pod


@pytest.mark.parametrize("text", ["uh", "Uh-huh.", "Hmm, hmm", "mhm", "Umm...", "oh", "Erm, uh"])
def test_filler_only_transcripts(text):
    assert vector_intents.is_filler(text)


@pytest.mark.parametrize("text", ["", "Oh hi", "hmm what time is it", "sit", "huh what"])
def test_real_requests_are_not_filler(text):
    assert not vector_intents.is_filler(text)


def test_a_backchannel_is_treated_like_nothing_said(monkeypatch):
    monkeypatch.setattr(vector_pod, "_robot_obj", None)
    monkeypatch.setattr(vector_pod, "_followup_until", 0.0)
    assert vector_pod.route("uh-huh", time.time()) == vector_pod.route("", time.time())


def test_fillers_never_repeat_and_are_synthesized_once(monkeypatch):
    calls = []
    monkeypatch.setattr(vector_pod, "tts", lambda w: calls.append(w) or ([0.0] * 160, 16000))
    monkeypatch.setattr(vector_pod, "_filler_pcm", {})
    monkeypatch.setattr(vector_pod, "_last_filler", "")
    got = [vector_pod.pick_filler()[0] for _ in range(20)]
    assert all(a != b for a, b in zip(got, got[1:])) and set(got) <= set(vector_pod.FILLERS)
    assert len(calls) == len(set(calls)) <= len(vector_pod.FILLERS)
    assert vector_pod.pick_filler(look=True)[0] in vector_pod.LOOK_FILLERS


def test_the_talk_prompt_offers_voice_games():
    import server
    assert "20 questions" in server.VECTOR_GAMES and "one question per reply" in server.VECTOR_GAMES


def test_his_name_alone_is_not_a_request():
    import vector_intents
    assert vector_intents.is_filler("Vector.") and vector_intents.is_filler("Rosie!") and vector_intents.is_filler("hmm, Vector")
    assert not vector_intents.is_filler("Vector, sit") and not vector_intents.is_filler("Rosie what time is it")
