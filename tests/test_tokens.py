# board #904 owner-token-report
import datetime as dt
import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import tokens  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "claude"
    (h / "projects" / "-proj-a").mkdir(parents=True)
    monkeypatch.setenv("ROSIE_CLAUDE_HOME", str(h))
    monkeypatch.setattr(tokens, "STATE", tmp_path / "token_report.json")
    monkeypatch.setattr(tokens, "_JOB", {})
    return h


def line(rid, model="claude-opus-5-5", out=100, inp=5, cr=1000, cw=50, ts=None, sid="s1", side=False, mid=None):
    ts = ts or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    d = {"type": "assistant", "timestamp": ts, "sessionId": sid, "isSidechain": side, "message": {"id": mid or "msg_" + rid, "model": model, "role": "assistant",
         "content": [{"type": "text", "text": "SECRET WORDS"}], "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}
    if rid:
        d["requestId"] = rid
    return json.dumps(d) + "\n"


def write(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(lines))
    return path


def today():
    return dt.date.today().isoformat()


def test_a_response_on_several_lines_counts_once(home):
    write(home / "projects/-proj-a/s1.jsonl", line("r1"), line("r1"), line("r1"), line("r2", out=40))
    st = tokens.refresh()
    cell = st["days"][today()]["opus"]["main"]
    assert cell[0] == 140 and cell[4] == 2
    assert "SECRET WORDS" not in tokens.STATE.read_text()  # board #904 safety-numbers-ids-only


def test_the_message_id_is_used_when_there_is_no_request_id(home):
    write(home / "projects/-proj-a/s1.jsonl", line("", mid="m1"), line("", mid="m1"), line("", mid="m2"))
    assert tokens.refresh()["days"][today()]["opus"]["main"][4] == 2


def test_main_thread_and_subagents_and_families(home):
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=100), line("r2", "claude-sonnet-5-5", out=10, side=True))
    write(home / "projects/-proj-a/s1/subagents/agent-x.jsonl", line("r3", "claude-haiku-5-5", out=7, sid="s1"), line("r4", "gpt-other", out=3, sid="s1"))
    d = tokens.refresh()["days"][today()]
    assert d["opus"]["main"][0] == 100 and d["sonnet"]["sub"][0] == 10 and d["haiku"]["sub"][0] == 7 and d["other"]["sub"][0] == 3
    rep = tokens.summary()
    assert rep["threads"] == {"main": 100, "sub": 20}
    assert [s["session"] for s in rep["top_sessions"]] == ["s1"] and rep["top_sessions"][0]["out"] == 120 and rep["top_sessions"][0]["sub_out"] == 20


def test_a_refresh_reads_only_new_bytes(home):
    f = write(home / "projects/-proj-a/s1.jsonl", line("r1", out=100))
    tokens.refresh()
    assert tokens._load()["files"][str(f)]["off"] == f.stat().st_size
    write(f, line("r1", out=100), line("r2", out=50))
    st = tokens.refresh()
    assert st["days"][today()]["opus"]["main"][0] == 150 and st["days"][today()]["opus"]["main"][4] == 2
    assert tokens.refresh()["days"][today()]["opus"]["main"][4] == 2


def test_a_half_written_line_waits_for_the_next_refresh(home):
    f = home / "projects/-proj-a/s1.jsonl"
    full = line("r1", out=100)
    write(f, line("r0", out=1), full[:40])
    assert tokens.refresh()["days"][today()]["opus"]["main"][0] == 1
    write(f, full[40:])
    assert tokens.refresh()["days"][today()]["opus"]["main"][0] == 101


def test_the_byte_cap_continues_next_time(home):
    f = write(home / "projects/-proj-a/s1.jsonl", *[line(f"r{i}", out=1) for i in range(20)])
    st = tokens.refresh(cap=len(line("r1")) * 5)
    assert 5 <= st["days"][today()]["opus"]["main"][4] < 20 and st["pending"] > 0
    st = tokens.refresh()
    assert st["days"][today()]["opus"]["main"][4] == 20 and st["pending"] == 0


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is Unix-only")
def test_days_are_local_days(home, monkeypatch):
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # board #904 quirk-utc-plus-fourteen
    time.tzset()
    try:
        write(home / "projects/-proj-a/s1.jsonl", line("r1", ts="2026-03-10T12:00:00Z"), line("r2", ts="2026-03-10T09:00:00Z"))
        assert tokens._day("2026-03-10T12:00:00Z") == "2026-03-11" and tokens._day("2026-03-10T09:00:00Z") == "2026-03-10"
        assert tokens._day("garbage") is None
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_the_ledger_is_counted_per_day_and_optional(home):
    assert tokens.refresh()["ledger"]["days"] == {}
    led = home / "skills/token-saver/ledger.jsonl"
    write(led, json.dumps({"ts": today() + "T09:00:00", "cmd": "sum", "tokens_offloaded": 1000, "tokens_returned": 100}) + "\n", "not json\n")
    st = tokens.refresh()
    assert st["ledger"]["days"][today()] == [900, 1000, 1]
    write(led, json.dumps({"ts": today() + "T10:00:00", "tokens_offloaded": 500, "tokens_returned": 50}) + "\n")
    assert tokens.summary(refresh_now=True)["ledger"]["today"] == 1350


def test_flags_follow_the_goals():
    g = dict(tokens.GOALS)
    base = {"out": 0, "in": 0, "cache_read": 0, "cache_write": 0, "main_share": 0, "cache_ratio": 99}
    day = lambda out: {"out": out}
    assert tokens.flags(dict(base, out=1000), dict(base, out=1_000_000, main_share=50), [day(1000)] * 7, g) == []
    f = tokens.flags(dict(base), dict(base, out=1_000_000, main_share=72), [], g)
    assert [x["id"] for x in f] == ["main_share"] and "72%" in f[0]["text"] and "Sonnet" in f[0]["text"]
    assert tokens.flags(dict(base), dict(base, out=1000, main_share=99), [], g) == []
    f = tokens.flags(dict(base, out=300_000), dict(base, out=1_000_000, main_share=10), [day(100_000)] * 7, g)
    assert [x["id"] for x in f] == ["spike"]
    assert tokens.flags(dict(base, out=190_000), dict(base, out=1_000_000, main_share=10), [day(100_000)] * 7, g) == []
    f = tokens.flags(dict(base), dict(base, out=1000, **{"in": 900_000, "cache_read": 500_000, "cache_ratio": 35}), [], g)
    assert [x["id"] for x in f] == ["cache"]
    g["token_goal_main_share"] = 80
    assert tokens.flags(dict(base), dict(base, out=1_000_000, main_share=72), [], g) == []


def test_the_goals_come_from_the_config(home, monkeypatch):
    import config
    monkeypatch.setattr(config, "load", lambda: {"token_goal_main_share": 30, "token_spike_factor": True})
    g = tokens._goals()
    assert g["token_goal_main_share"] == 30 and g["token_spike_factor"] == 2.0 and g["token_goal_cache_read_ratio"] == 80


def test_hook_line_format_and_speed(home):
    assert tokens.hook_line() is None
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=120_000), line("r2", "claude-sonnet-5-5", out=80_000, side=True))
    tokens.refresh()
    t0 = time.time()
    ln = tokens.hook_line()
    assert time.time() - t0 < 0.3
    assert ln.startswith("Tokens: today 200k out (main 60%), 7 days 200k; goal main ≤ 50% — The main model wrote 60%") and "as of" not in ln and "\n" not in ln
    st = json.loads(tokens.STATE.read_text())
    st["at"] = time.time() - 7200
    tokens.STATE.write_text(json.dumps(st))
    assert tokens.hook_line().endswith(time.strftime("(as of %H:%M)", time.localtime(st["at"])))


def test_the_session_start_hook_adds_the_line(home, monkeypatch):
    import board_hook
    import agentboard
    monkeypatch.setattr(agentboard, "checkin", lambda **k: None)
    monkeypatch.setattr(agentboard, "summary", lambda: "BOARD")
    monkeypatch.setattr(agentboard, "me", lambda: "claude-1")
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=1000))
    tokens.refresh()
    out = board_hook.main("SessionStart", {"cwd": "/x", "source": "startup"})
    assert out and "BOARD" in out["hookSpecificOutput"]["additionalContext"] and "\nTokens: today " in out["hookSpecificOutput"]["additionalContext"]


def test_the_job_refreshes_hourly_and_puts_one_inbox_item_a_day(home):
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=900_000))
    got = []
    add = lambda *a, **k: got.append((a, k))
    now = dt.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    tokens.job(now, {}, add=add, background=False)
    assert len(got) == 1 and got[0][1]["key"] == f"tokens:{now:%Y-%m-%d}" and got[0][0][3] == "#/tokens" and got[0][1]["desktop"] is False
    tokens.job(now + dt.timedelta(minutes=30), {}, add=add, background=False)
    assert len(got) == 1
    tokens.job(now + dt.timedelta(minutes=61), {}, add=add, background=False)  # board #904 why-inbox-dedupes-key
    assert all(g[1]["key"] == got[0][1]["key"] for g in got)
    tokens._JOB.clear()
    before = len(got)
    tokens.job(now, {"token_report_on": False}, add=add, background=False)
    assert len(got) == before and not tokens._JOB


def test_the_job_is_registered():
    import automations
    assert automations.job_tokens in automations.JOBS and automations.JOBS[-1] is automations.job_mail


def _login(c):
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())


def test_the_api_needs_the_login_and_has_the_shape(home, tmp_path, monkeypatch):
    import server
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", tmp_path / "secrets")
    c = TestClient(server.app)
    assert c.get("/api/tokens").status_code == 401
    _login(c)
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=500))
    tokens.refresh()
    r = c.get("/api/tokens").json()
    assert {"today", "week", "month", "series", "by_model", "threads", "top_sessions", "ledger", "flags", "goals", "updated"} <= set(r)
    assert len(r["series"]) == 14 and r["series"][-1]["day"] == today() and r["series"][-1]["out"] == 500
    assert r["today"]["out"] == 500 and r["by_model"]["opus"] == 500 and r["goals"]["token_goal_main_share"] == 50
    assert c.post("/api/tokens", json={}).status_code in (404, 405)


def test_the_cli_prints_the_summary(home, tmp_path):
    write(home / "projects/-proj-a/s1.jsonl", line("r1", out=500))
    env = {"ROSIE_CLAUDE_HOME": str(home), "ROSIE_TESTING": "1", "PATH": "/usr/bin:/bin"}
    code = ("import sys; sys.path.insert(0, %r); import tokens; tokens.STATE = __import__('pathlib').Path(%r); "
            "tokens.refresh(); print(tokens.text(tokens.summary()))" % (str(ROOT / "app"), str(tmp_path / "cli.json")))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60).stdout
    assert "Today:   500 out" in out and "Flags:" in out
