import actions
import config
import phone


def set_cfg(**kw):
    c = config.load()
    c.update(kw)
    config.save(c)


def test_text_asks_first_by_default_and_uses_the_name(fake_phone):
    q = phone.send_sms("Mom", "see you at six")
    assert q["status"] == "needs_confirmation" and q["summary"] == 'Text Mom: "see you at six"' and q["auto"] is False
    assert not fake_phone.did("sms")                             # nothing sent until the user says yes


def test_same_person_two_numbers_uses_the_mobile(fake_phone):
    q = phone.send_sms("Mom", "hi")
    assert actions._pending[q["token"]]["payload"]["args"]["to"] == "+15551110002"


def test_different_people_get_exactly_one_question(fake_phone):
    r = phone.send_sms("Sam", "hello")
    assert r["ask_user"] and r["question"] == "Which one: Sam Lee or Sam Ortiz?"
    assert not fake_phone.did("sms")


def test_a_looked_up_number_is_refused_unless_the_user_said_it(fake_phone):
    phone.user_text = "text Sam hello"
    r = phone.send_sms("+15553330001", "hello")
    assert "Do not use a phone number" in r["error"]
    phone.user_text = "text 555 333 0001 hello"
    assert phone.send_sms("+15553330001", "hello")["status"] == "needs_confirmation"


def test_unknown_contact(fake_phone):
    assert "could not find" in phone.send_sms("Zoltan", "hi")["error"]


def test_sms_modes(fake_phone):
    set_cfg(sms_confirm="always")
    assert phone.send_sms("Mom", "hi")["auto"] is True
    set_cfg(sms_confirm="headphones")
    assert phone.send_sms("Mom", "hi")["auto"] is False
    fake_phone.headset = True
    assert phone.send_sms("Mom", "hi")["auto"] is True


def test_call_modes(fake_phone):
    q = phone.call("Mom")
    assert q["summary"] == "Calling Mom" and q["auto"] is True and not fake_phone.did("call")       # announce, then a cancel window
    set_cfg(call_confirm="ask")
    assert phone.call("Mom")["auto"] is False
    set_cfg(call_confirm="now")
    r = phone.call("Mom")
    assert fake_phone.did("call")[0]["args"]["to"] == "+15551110002" and "Calling Mom" in r["note"]


def test_confirming_runs_the_job_and_cancelling_does_not(fake_phone):
    import agent  # registers the executor for confirmed tools
    q = phone.send_sms("Mom", "hi")
    actions.cancel(q["token"])
    assert not fake_phone.did("sms")
    q = phone.send_sms("Mom", "hi")
    assert actions.confirm(q["token"])["status"] == "done"
    assert fake_phone.did("sms")[0]["args"] == {"to": "+15551110002", "text": "hi"}


def test_phone_offline_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(phone, "online", lambda: False)
    assert "not connected" in phone.run("status")["error"]
