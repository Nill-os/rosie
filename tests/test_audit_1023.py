import datetime as dt
import time

import automations
import briefs
import gcal
import inbox
import workflows

NOW = dt.datetime(2026, 10, 23, 14, 0, tzinfo=gcal.TZ)


def test_a_failed_auto_sort_is_tried_once_per_version_of_the_pad_and_says_it_failed(monkeypatch):
    workflows.SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    workflows.SCRATCH.write_text("buy milk\ncall mom\nfix bike\n")
    old = time.time() - 3600
    import os
    os.utime(workflows.SCRATCH, (old, old))
    calls, notes = [], []
    monkeypatch.setattr(workflows, "sync", lambda: (calls.append(1), "Sync done.\n- sync_scratch FAILED and was left untouched: ConnectError")[1])
    monkeypatch.setattr(inbox, "add", lambda kind, title, *a, **k: notes.append(title))
    monkeypatch.setattr(automations, "_scratch_tried", {})
    cfg = {"scratch_mode": "auto", "scratch_min_lines": 3, "scratch_idle_min": 30}
    for m in range(5):
        automations.job_scratch(NOW + dt.timedelta(minutes=m), cfg)
    assert len(calls) == 1 and notes == ["Couldn't sort your scratch pad"]
    workflows.SCRATCH.write_text("buy milk\ncall mom\nfix bike\nnew line\n")
    os.utime(workflows.SCRATCH, (old, old + 1))
    automations.job_scratch(NOW + dt.timedelta(minutes=9), cfg)
    assert len(calls) == 2


def test_the_brief_is_made_even_when_the_calendar_cant_be_read(monkeypatch):
    def down(day):
        raise OSError("no network yet")
    monkeypatch.setattr(gcal, "events_on", down)
    text = briefs.build_text(NOW.date())
    assert "The calendar couldn't be read (OSError)" in text


def test_a_late_tick_still_reminds_of_an_event_that_just_started(monkeypatch):
    start = NOW + dt.timedelta(minutes=-2)
    monkeypatch.setattr(automations, "_events_today", lambda now: [{"id": "e1", "title": "Dentist", "start": start.isoformat()}])
    added = []
    monkeypatch.setattr(inbox, "add", lambda kind, title, body, *a, **k: added.append((title, body)))
    automations.job_events(NOW, {"remind_events": True, "remind_lead_min": 15})
    assert added == [(f"{start:%H:%M} Dentist", "Starting now.")]
    added.clear()
    monkeypatch.setattr(automations, "_events_today", lambda now: [{"id": "e1", "title": "Dentist", "start": (NOW - dt.timedelta(minutes=6)).isoformat()}])
    automations.job_events(NOW, {"remind_events": True, "remind_lead_min": 15})
    assert added == []


def test_mail_triage_runs_off_the_tick(monkeypatch):
    import threading
    import gmail
    import mailwatch
    gate, done = threading.Event(), threading.Event()
    monkeypatch.setattr(gmail, "accounts", lambda: ["me@example.com"])
    monkeypatch.setattr(mailwatch, "check", lambda now: (gate.wait(5), done.set()))
    monkeypatch.setattr(automations, "_mail", {"t": 0.0})
    t0 = time.time()
    automations.job_mail(NOW, {"mail_on": True, "mail_check_min": 60})
    assert time.time() - t0 < 1 and automations._mail["running"]
    automations._mail["t"] = 0.0
    automations.job_mail(NOW, {"mail_on": True, "mail_check_min": 60})
    gate.set()
    assert done.wait(5)
    for _ in range(50):
        if not automations._mail["running"]:
            break
        time.sleep(0.05)
    assert automations._mail["running"] is False


def test_tomorrow_in_an_email_means_the_day_after_it_was_sent():
    import mailwatch
    sent = dt.datetime(2026, 10, 23, 9, 0, tzinfo=gcal.TZ)
    msg = {"id": "m1", "from": "Sam <sam@example.com>", "subject": "Report", "ts": int(sent.timestamp())}
    mailwatch._propose("me", msg, {"tasks": [{"title": "Send the report", "when": "tomorrow"}], "events": []}, {})
    assert mailwatch.todos()[0]["due"] == "2026-10-24"


def test_llm_json_answers_have_a_length_cap(monkeypatch):
    import httpx
    sent = []

    def post(url, timeout=None, json=None):
        sent.append(json)
        return httpx.Response(200, json={"message": {"content": "{}"}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(workflows.httpx, "post", post)
    workflows.llm_json("s", "u", {"type": "object"})
    assert sent[0]["options"]["num_predict"] == workflows.LLM_JSON_PREDICT


def test_a_helper_process_asks_for_the_owners_model_not_the_default(monkeypatch):
    import config
    import corner_browse
    import mind
    import paper
    monkeypatch.setattr(corner_browse, "LIVE", {"set": False})  # board #848 quirk-helper-no-load-saved
    cfg = config.load()
    cfg["chat_model"] = "huihui_ai/qwen3.5-abliterated:9b"
    config.save(cfg)
    assert mind._model() == "huihui_ai/qwen3.5-abliterated:9b" and paper.Model().model == "huihui_ai/qwen3.5-abliterated:9b"
    monkeypatch.setattr(corner_browse, "LIVE", {"set": True})  # board #848 why-server-own-pick
    assert mind._model() == corner_browse.MODEL


def test_agent_zeros_line_starts_on_a_later_try_when_its_address_was_busy():
    # board #848 fix-retry-fresh-app
    import socket
    import threading
    import a0_mcp
    from mcp.server.mcpserver import MCPServer
    mcp_server = MCPServer("t")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    tries, served = [], threading.Event()

    def make_app():
        tries.append(1)
        if len(tries) == 2:
            blocker.close()
        return mcp_server.streamable_http_app(stateless_http=True, json_response=True)

    def on_server(srv):
        def watch():
            for _ in range(100):
                if srv.started:
                    served.set()
                    srv.should_exit = True
                    return
                time.sleep(0.05)
        threading.Thread(target=watch, daemon=True).start()
    t = threading.Thread(target=a0_mcp._serve, args=(make_app, "127.0.0.1", port, 0.1, on_server), daemon=True)
    t.start()
    t.join(15)
    assert served.is_set() and len(tries) == 2 and not t.is_alive(), (len(tries), t.is_alive())


def test_reading_waits_while_mail_is_checked(monkeypatch):
    monkeypatch.setattr(automations, "_mail", {"t": 0.0, "running": True})
    monkeypatch.setattr(automations, "_timer_busy", lambda: None)
    assert automations.corner_busy(NOW) == "Checking mail."


def test_the_owners_model_is_loaded_once_ollama_answers(monkeypatch):
    import models
    tried = []
    monkeypatch.setattr(models, "last_problem", "Couldn't reach Ollama to load x (ConnectError); using qwen3.5:9b.")
    monkeypatch.setattr(models, "load_saved", lambda: tried.append(1))
    monkeypatch.setattr(automations, "_model_retry", {"t": 0.0})
    automations.job_model(NOW, {})
    automations.job_model(NOW, {})  # board #848 why-job-model-once-minute
    assert tried == [1]
    monkeypatch.setattr(models, "last_problem", "")
    monkeypatch.setattr(automations, "_model_retry", {"t": 0.0})
    automations.job_model(NOW, {})
    assert tried == [1]


def test_an_embedding_outage_is_a_wait_for_her_mind(monkeypatch):
    import httpx
    import mind
    import pytest

    def down(request):
        raise httpx.ConnectError("Ollama is restarting")
    monkeypatch.setattr(mind, "_transport", httpx.MockTransport(down))
    with pytest.raises(mind.Busy):
        mind.embed(["a fact"])


def test_helpers_follow_the_model_the_server_really_runs(monkeypatch):
    # board #848 fix-saved-model-default
    import config
    import corner_browse
    import ctxsize
    import models
    cfg = config.load()
    cfg["chat_model"] = "some/unusable:9b"
    config.save(cfg)
    monkeypatch.setattr(corner_browse, "LIVE", {"set": False})
    monkeypatch.setattr(models, "installed", lambda: [{"name": "some/unusable:9b", "usable": False, "why_not": "can't use tools (Rosie needs them)"}])
    monkeypatch.setattr(models, "last_problem", "")
    monkeypatch.setattr(models.agent, "MODEL", models.DEFAULT)
    models.load_saved()
    assert models.current() == models.DEFAULT and "can't use tools" in models.last_problem
    monkeypatch.setattr(corner_browse, "LIVE", {"set": False})
    assert ctxsize.live_model() == models.DEFAULT
