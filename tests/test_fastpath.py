import fastpath
import inbox
import tasks


def test_time_and_date():
    import datetime as dt
    now = dt.datetime(2026, 10, 4, 15, 5)
    assert fastpath.try_fast("what time is it", now)["reply"] == "It's 3:05 PM."
    assert "Sunday, October 4" in fastpath.try_fast("what's the date", now)["reply"]


def test_reminder_with_time_at_end_and_start():
    r = fastpath.try_fast("Remind me to call the bank at 4 PM")
    assert r["tool"] == "set_reminder" and "call the bank" in r["reply"] and "4:00 PM" in r["reply"]
    r = fastpath.try_fast("remind me to stretch in 20 minutes")
    assert r["result"]["text"] == "stretch"
    assert len(inbox.list_reminders()) == 2


def test_reminder_without_a_time_is_left_to_the_ai():
    assert fastpath.try_fast("remind me to call the bank") is None


def test_add_task():
    r = fastpath.try_fast("add milk to my to-do list")
    assert r["tool"] == "add_task" and [t["title"] for t in tasks.list_for()] == ["milk"]
    assert fastpath.try_fast("add task buy stamps tomorrow")["args"]["due"]


def test_ordinary_chat_is_not_hijacked(fake_phone):
    for t in ("tell me a joke", "tell me about my day", "call me back later", "how do I text faster", "what should I do now", "open the terminal"):
        assert fastpath.try_fast(t) is None, t


def test_phone_commands_when_phone_is_online(fake_phone):
    assert fastpath.try_fast("turn on the flashlight")["reply"] == "Flashlight on."
    assert fake_phone.did("torch")[0]["args"] == {"on": True}
    assert fastpath.try_fast("pause the music")["reply"] == "Paused."
    assert fastpath.try_fast("volume up")["tool"] == "phone_volume"
    r = fastpath.try_fast("set an alarm for 6:45 am")
    assert r["reply"] == "Alarm set for 6:45 AM." and fake_phone.did("alarm")[0]["args"]["time"] == "06:45"
    assert fastpath.try_fast("set a timer for 10 minutes")["reply"] == "Timer set for 10 minutes."
    assert fastpath.try_fast("add eggs and bread to my keep")["reply"].startswith("Saved to Keep")


def test_phone_commands_fall_through_when_offline(monkeypatch):
    import phone
    monkeypatch.setattr(phone, "online", lambda: False)
    assert fastpath.try_fast("turn on the flashlight") is None
    assert fastpath.try_fast("text Mom saying hi") is None


def test_text_and_call_phrases_go_to_confirmation_not_straight_out(fake_phone):
    r = fastpath.try_fast("text Mom saying I'll be there at six")
    assert r["pending"] and "Mom" in r["reply"] and not fake_phone.did("sms")
    r = fastpath.try_fast("tell Mom that I'm running late")
    assert r["pending"] and "running late" in r["pending"][0]["summary"]
    r = fastpath.try_fast("call Mom")
    assert r["pending"][0].get("auto") is True and not fake_phone.did("call")


def test_pronouns_and_non_names_are_not_texted(fake_phone):
    assert fastpath.try_fast("tell her that it's fine") is None
    assert fastpath.try_fast("tell me that it works") is None
