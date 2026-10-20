import actions
import phone
import screenagent
import workflows


def script(monkeypatch, steps):
    it = iter(steps)
    monkeypatch.setattr(workflows, "llm_json", lambda system, user, schema, timeout=300: next(it))
    monkeypatch.setattr(screenagent.time, "sleep", lambda s: None)


def test_notifications_and_reply_readback(fake_phone):
    n = phone.notifications()
    assert n["notifications"][0]["from"] == "Sam"
    q = phone.reply_notification("n1", "yes, 6 works")
    assert q["status"] == "needs_confirmation" and q["summary"] == 'Reply to Sam in WhatsApp: "yes, 6 works"' and not fake_phone.did("notif_reply")


def test_reply_to_a_notification_without_a_reply_box_is_refused(fake_phone):
    assert "no reply box" in phone.reply_notification("n2", "hi")["error"]
    assert "gone" in phone.reply_notification("zzz", "hi")["error"]


def test_confirmed_reply_goes_out(fake_phone):
    import agent
    q = phone.reply_notification("n1", "on my way")
    assert actions.confirm(q["token"])["status"] == "done"
    assert fake_phone.did("notif_reply")[0]["args"] == {"id": "n1", "text": "on my way"}


def test_risky_tap_asks_first_and_runs_only_after_confirm(fake_phone):
    import agent
    q = phone.screen_tap("5")
    assert q["status"] == "needs_confirmation" and q["summary"] == "Tap 'Send' in WhatsApp"
    assert q["auto"] is False and fake_phone.did("screen_tap")[0]["args"]["force"] is False
    actions.confirm(q["token"])
    assert fake_phone.did("screen_tap")[-1]["args"]["force"] is True


def test_safe_tap_just_happens(fake_phone):
    assert phone.screen_tap("0")["done"]


def test_screen_agent_finishes_the_goal(fake_phone, monkeypatch):
    script(monkeypatch, [{"action": "tap", "target": "0", "why": "open WhatsApp"}, {"action": "done", "why": "WhatsApp is open"}])
    r = screenagent.run("open WhatsApp")
    assert r["done"] and "WhatsApp is open" in r["summary"] and len(r["steps_done"]) == 1
    assert fake_phone.did("screen_tap")


def test_screen_agent_stops_at_a_risky_button(fake_phone, monkeypatch):
    script(monkeypatch, [{"action": "tap", "target": "5", "why": "send it"}, {"action": "done", "why": "should never be reached"}])
    r = screenagent.run("send the message")
    assert r["status"] == "needs_confirmation" and "Send" in r["summary"]


def test_screen_agent_gives_up_instead_of_looping(fake_phone, monkeypatch):
    script(monkeypatch, [{"action": "scroll", "direction": "down", "why": "look"}] * 5)
    r = screenagent.run("find something")
    assert "stuck" in r["error"]


def test_screen_agent_respects_blocked_screens_and_stop(fake_phone, monkeypatch):
    fake_phone.screen_error = "That screen (com.bank.app) is off limits for safety"
    script(monkeypatch, [{"action": "done", "why": "x"}])
    assert "off limits" in screenagent.run("check my balance")["error"]


def test_screen_agent_is_bounded(fake_phone, monkeypatch):
    script(monkeypatch, [{"action": "scroll", "direction": "down" if i % 2 else "up", "why": f"s{i}"} for i in range(40)])
    r = screenagent.run("never ends", max_steps=50)
    assert len(r["steps_done"]) <= screenagent.MAX_STEPS and "error" in r
