"""Rosie's model scout (model_scout.py): which new abliterated models she keeps as candidates, when she tests one, and when she flags it."""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import corner  # noqa: E402
import model_scout as ms  # noqa: E402

CURRENT = "huihui_ai/qwen3.5-abliterated:9b"
NIGHT = dt.datetime(2026, 10, 23, 1, 0).astimezone()


def hf(rid, params=8e9, base=None, created="2026-10-17T09:00:00.000Z", downloads=100, likes=0):
    return {"id": rid, "gguf": {"total": params, "architecture": "x"}, "createdAt": created, "downloads": downloads, "likes": likes,
            "cardData": {"base_model": base} if base else {}}


def files(size=4.9e9, quant="Q4_K_M"):
    return [{"type": "file", "path": f"model.{quant}.gguf", "size": size}, {"type": "file", "path": "README.md", "size": 10}]


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(ms, "STATE", tmp_path / "model_scout.json")
    monkeypatch.setattr(ms.models, "current", lambda: CURRENT)
    monkeypatch.setattr(ms, "state", {"running": False, "doing": "", "last_error": ""})
    monkeypatch.setattr(corner, "GPU_HOLD", "")
    monkeypatch.setattr(corner, "owner_recent", lambda s: False)
    monkeypatch.setattr(ms.shutil, "disk_usage", lambda p: type("U", (), {"free": 100e9})())
    monkeypatch.setattr(corner, "STAGE_FLAG", tmp_path / "no-staging")      # never the real PC's load, jobs or stage flag
    monkeypatch.setattr(corner.os, "getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)
    monkeypatch.setattr(corner, "_busy_probe", None)


def test_only_chat_models_that_fit_her_graphics_card_and_are_new_become_candidates():
    j = lambda m, f=None, bases=(): ms.judge(m, f, CURRENT, set(bases))
    assert j(hf("a/Qwen3.8-27B-abliterated-GGUF", 27e9))["why"].startswith("27B parameters")
    assert j(hf("a/Qwen3.5-2B-abliterated-GGUF", 2e9))["status"] == "skipped"
    assert j(hf("a/Llama-8B-abliterated-LoRA"))["why"] == "not a chat model"
    assert j(hf("a/x-GGUF", base="dalatexcoder/Rice-Cracker-Qwen3.5-8B-Abliterated-Base"))["why"] == "not a chat model"
    assert j(hf("b/Huihui-Qwen3.5-9B-abliterated-GGUF", 9.6e9, base="huihui-ai/Huihui-Qwen3.5-9B-abliterated"))["why"] == "the same model she is on"
    assert j(hf("c/Llama-3.1-8B-abliterated-GGUF", base="meta-llama/llama-3.1-8b"), bases={"meta-llama/llama-3.1-8b"})["why"] == "a copy of one already found"
    assert j(hf("d/m-GGUF"))["status"] == ""                                            # passes the cheap checks: its files are looked at next
    assert j(hf("d/m-GGUF"), files(quant="Q8_0"))["why"] == "no Q4_K_M file"
    assert "too big for the graphics card" in j(hf("d/m-GGUF"), files(7.4e9))["why"]
    c = j(hf("d/m-GGUF"), files(4.9e9))
    assert c["status"] == "candidate" and c["pull"] == "hf.co/d/m-GGUF:Q4_K_M" and c["file_gb"] == 4.9


def test_research_judges_each_model_once_and_keeps_one_copy_of_each(monkeypatch):
    asked = []
    listing = [hf("a/Llama-3.1-8B-abliterated-GGUF", base="meta-llama/llama-3.1-8b", downloads=50),
               hf("b/Llama-3.1-8B-abliterated-i1-GGUF", base="meta-llama/llama-3.1-8b"),
               hf("c/Big-30B-abliterated-GGUF", 30e9)]

    def get(url):
        asked.append(url)
        return listing if "search=" in url else files()
    monkeypatch.setattr(ms, "_get_json", get)
    assert ms.research() == 1
    seen = ms.load()["seen"]
    assert seen["a/Llama-3.1-8B-abliterated-GGUF"]["status"] == "candidate"
    assert seen["b/Llama-3.1-8B-abliterated-i1-GGUF"]["why"] == "a copy of one already found"
    assert len(asked) == 2                                                               # one search, one file list (the big one isn't opened)
    asked.clear()
    assert ms.research() == 0 and len(asked) == 1                                       # already judged: not looked at again
    assert not ms.research_due(ms.load(), ms._now()) and ms.research_due({}, ms._now())


def test_the_best_candidate_a_day_old_is_tested_first():
    d = {"seen": {"new/x": {"status": "candidate", "created": "2026-10-22T20:00:00Z", "downloads": 9000, "likes": 9},
                  "old/y": {"status": "candidate", "created": "2026-10-17T09:00:00Z", "downloads": 300, "likes": 0},
                  "old/z": {"status": "candidate", "created": "2026-10-17T09:00:00Z", "downloads": 100, "likes": 5},
                  "old/t": {"status": "tested", "created": "2026-10-10T09:00:00Z", "downloads": 99999, "likes": 99}}}
    assert ms.next_candidate(d, NIGHT)[0] == "old/z"                                    # 100 + 5 likes beats 300; the 9000 one is too new
    assert ms.next_candidate({"seen": {}}, NIGHT) is None


def test_she_tests_only_at_night_while_the_owner_is_away_once_a_night(monkeypatch):
    assert ms.test_due({}, NIGHT) is None
    assert ms.test_due({}, NIGHT.replace(hour=2, minute=30)) == "outside the test window"   # the hair-clip batch has the PC from 2:00
    assert ms.test_due({"tested_on": "2026-10-23"}, NIGHT) == "already tested one tonight"
    monkeypatch.setattr(corner, "GPU_HOLD", "Paused while the context window is being tuned.")
    assert ms.test_due({}, NIGHT) == corner.GPU_HOLD
    monkeypatch.setattr(corner, "GPU_HOLD", "")
    monkeypatch.setattr(ms.shutil, "disk_usage", lambda p: type("U", (), {"free": 10e9})())
    assert ms.test_due({}, NIGHT) == "not enough free disk space"
    monkeypatch.setattr(corner, "owner_recent", lambda s: True)
    assert ms.test_due({}, NIGHT) == "the owner was here recently"


def test_better_means_more_right_then_fewer_questions_then_clearly_faster():
    s = lambda right, clean=13, secs=100: {"right": right, "of": 13, "clean": clean, "secs": secs}
    assert ms.better(s(13), s(12)) and not ms.better(s(12), s(13))
    assert ms.better(s(12, 13), s(12, 11)) and not ms.better(s(12, 11), s(12, 13))
    assert ms.better(s(12, 13, 70), s(12, 13, 100)) and not ms.better(s(12, 13, 90), s(12, 13, 100))


def test_the_benchmark_score_line_is_read_as_model_eval_prints_it():
    m = ms.SCORE.search(f"  -> {12}/{13} correct, {11}/{13} without needless questions, {143:.0f}s total")
    assert m and m.groups() == ("12", "13", "11", "143")


@pytest.fixture
def lab(monkeypatch):
    """A fake Ollama and benchmark for test()."""
    lab = {"pulled": [], "deleted": [], "caps": ["completion", "tools"], "evals": [], "inbox": [], "warm": 0, "holds": []}
    monkeypatch.setattr(ms, "_pull", lambda n: lab["pulled"].append(n))
    monkeypatch.setattr(ms, "_delete", lambda n: lab["deleted"].append(n))
    monkeypatch.setattr(ms, "_caps", lambda n: lab["caps"])
    monkeypatch.setattr(ms, "_installed", lambda n: lab.get("owners", False))

    def run_eval(cur, cand):
        lab["holds"].append(corner.GPU_HOLD)
        r = lab["evals"].pop(0)
        if isinstance(r, Exception):
            raise r
        return {cur: r[0], cand: r[1]}
    monkeypatch.setattr(ms, "run_eval", run_eval)
    import corner_browse
    import inbox
    monkeypatch.setattr(inbox, "add", lambda *a, **k: lab["inbox"].append((a, k)))
    monkeypatch.setattr(corner_browse, "warm_chat", lambda: lab.__setitem__("warm", lab["warm"] + 1))
    return lab


REC = {"status": "candidate", "pull": "hf.co/d/m-GGUF:Q4_K_M", "base": "d/m", "created": "2026-10-17T09:00:00Z"}
sc = lambda right, clean=13, secs=130: {"right": right, "of": 13, "clean": clean, "secs": secs}


def test_a_model_that_wins_twice_is_flagged_and_kept(lab):
    lab["evals"] = [(sc(12), sc(13)), (sc(12), sc(13))]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    r = ms.load()["seen"]["d/m-GGUF"]
    assert r["status"] == "flagged" and r["score"] == "26/26" and r["current_score"] == "24/26" and r["runs"] == 2
    assert lab["deleted"] == [] and len(lab["inbox"]) == 1 and "Settings > AI model" in lab["inbox"][0][0][2]
    assert lab["holds"] == [ms.HOLD, ms.HOLD] and corner.GPU_HOLD == "" and lab["warm"] == 1   # corner and graph waited, then go on
    assert ms.load()["tested_on"] == "2026-10-23" and ms.status()["flagged"] == ["d/m-GGUF"]


def test_a_model_that_isnt_better_is_deleted_without_a_second_run(lab):
    lab["evals"] = [(sc(13), sc(11))]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    r = ms.load()["seen"]["d/m-GGUF"]
    assert r["status"] == "tested" and r["score"] == "11/13" and r["runs"] == 1
    assert lab["deleted"] == ["hf.co/d/m-GGUF:Q4_K_M"] and lab["inbox"] == [] and corner.GPU_HOLD == ""


def test_a_lucky_first_run_isnt_enough(lab):
    lab["evals"] = [(sc(12), sc(13)), (sc(13), sc(12))]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    assert ms.load()["seen"]["d/m-GGUF"]["status"] == "tested" and lab["inbox"] == [] and lab["deleted"]


def test_a_model_without_tools_is_never_benchmarked(lab):
    lab["caps"] = ["completion"]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    r = ms.load()["seen"]["d/m-GGUF"]
    assert r["status"] == "failed" and "tools" in r["why"] and lab["holds"] == [] and lab["deleted"]


def test_the_owner_coming_back_stops_the_test_and_it_is_tried_another_night(lab):
    lab["evals"] = [ms.Interrupted("you came back")]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    d = ms.load()
    assert d["seen"]["d/m-GGUF"]["status"] == "candidate" and d["tested_on"] == ""
    assert corner.GPU_HOLD == "" and lab["deleted"] and lab["warm"] == 1


def test_the_job_never_runs_under_tests_and_can_be_turned_off(monkeypatch):
    started = []
    monkeypatch.setattr(ms.threading, "Thread", lambda **k: started.append(k) or type("T", (), {"start": lambda s: None})())
    ms.job(NIGHT, {"model_scout": True})
    assert started == []                                                    # under pytest: never the real Hugging Face or Ollama
    monkeypatch.setattr(ms.testguard, "active", lambda: False)
    ms.job(NIGHT, {"model_scout": False})
    assert started == []
    ms.job(NIGHT, {"model_scout": True})
    assert started and ms.state["running"]
    called = []
    monkeypatch.setattr(ms, "_research_thread", lambda: called.append(1) or 1 / 0)    # even a crash in it
    ms.state["running"] = False
    started.clear()
    ms.job(NIGHT, {"model_scout": True})
    started[0]["target"]()
    assert called == [1] and ms.state["running"] is False and "ZeroDivisionError" in ms.state["last_error"]


def test_a_model_the_owner_already_had_is_never_deleted(lab):
    lab["owners"] = True
    lab["evals"] = [(sc(13), sc(11))]
    ms.test("d/m-GGUF", dict(REC), NIGHT)
    assert ms.load()["seen"]["d/m-GGUF"]["status"] == "tested" and lab["deleted"] == []


def test_a_stage_check_stops_the_scout(monkeypatch, tmp_path):
    flag = tmp_path / "staging-now"
    monkeypatch.setattr(corner, "STAGE_FLAG", flag)
    assert ms.test_due({}, NIGHT) is None and not ms._staging()
    flag.write_text("x")
    assert ms.test_due({}, NIGHT) == "Paused while a new version of Rosie is tested." and ms._staging()
