import json

import eval_metrics as em


def test_score_row_splits_the_four_metrics():
    p = "Remind me to call the bank at 4 PM today"
    assert em.score_row(p, [("set_reminder", {"text": "call the bank", "when": "4pm"})]) == \
        {"tool_name": True, "args_parse": True, "required_keys": True, "args_match": True}
    assert em.score_row(p, [("get_weather", {})])["tool_name"] is False
    r = em.score_row(p, [("set_reminder", "{not json")])
    assert r["tool_name"] and not r["args_parse"] and not r["required_keys"]
    r = em.score_row(p, [("set_reminder", json.dumps({"text": "call the bank"}))])
    assert r["args_parse"] and not r["required_keys"] and not r["args_match"]
    r = em.score_row(p, [("set_reminder", {"text": "call someone", "when": "5pm"})])
    assert r["required_keys"] and not r["args_match"]


def test_prompts_without_a_tool_are_not_scored():
    assert em.score_row("Give me one sentence of encouragement", []) is None


def test_summary_and_history_line(tmp_path):
    rows = [{"metrics": em.score_row("Text Mom that I'll be there", [("phone_send_sms", {"to": "Mom", "text": "x"})])},
            {"metrics": em.score_row("Set an alarm for 6:45", [("phone_set_alarm", {"time_24h": "07:00"})])},
            {"metrics": None}]
    counts = em.summarize(rows)
    assert counts == {"tool_name": [2, 2], "args_parse": [2, 2], "required_keys": [2, 2], "args_match": [1, 2]}
    assert "args_match 1/2" in em.format_counts(counts)
    path = tmp_path / "data" / "eval_history.jsonl"
    em.append_history(path, "m", 12, 13, 13, counts, 100.0, "2026-10-28T00:00:00")
    em.append_history(path, "m", 13, 13, 13, counts, 90.0, "2026-10-28T01:00:00")
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(lines) == 2 and lines[1]["correct"] == 13 and lines[0]["metrics"]["args_match"] == {"ok": 1, "of": 2}
