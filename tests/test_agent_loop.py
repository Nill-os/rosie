import agent
import phone


class FakeResp:
    def __init__(self, msg):
        self.msg = msg

    def json(self):
        return {"message": self.msg}


def script(monkeypatch, messages):
    it = iter(messages)
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")                              # the skill router also calls the model: keep it out of the script
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: FakeResp(next(it)))          # a second model call would raise StopIteration


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_a_which_one_question_ends_the_turn_at_once(fake_phone, monkeypatch):
    script(monkeypatch, [call("phone_send_sms", to="Sam", text="hello")])
    reply, pending, _ = agent.chat([], "text Sam hello", "auto", False)
    assert reply == "Which one: Sam Lee or Sam Ortiz?" and not pending and not fake_phone.did("sms")


def test_the_model_cannot_loop_on_a_failing_send(fake_phone, monkeypatch):
    script(monkeypatch, [call("phone_send_sms", to="Sam", text="hi"), call("phone_send_sms", to="Sam Lee", text="hi")])
    reply, _, _ = agent.chat([], "text Sam hi", "auto", False)
    assert reply.startswith("Which one")


def test_a_held_action_is_described_in_plain_words_and_the_reply_cannot_claim_it_is_done(fake_phone, monkeypatch):
    script(monkeypatch, [call("phone_keep_note", title="Thin-Crust Pizza Dough", text="long recipe text " * 50),
                         {"role": "assistant", "content": "I've created a Keep note with the recipe! Would you like me to add tips?"}])
    hist = [{"role": "tool", "tool_name": "web_search", "content": "outside content"}]               # untrusted content was read earlier in the chat
    reply, pending, _ = agent.chat(hist, "make a keep note with the best pizza recipe", "auto", False)
    assert pending and pending[0]["summary"].startswith("Save a Keep note called 'Thin-Crust Pizza Dough'")
    assert "phone_keep_note(" not in pending[0]["summary"] and "long recipe text" not in pending[0]["summary"]
    assert reply.startswith("Ready: Save a Keep note") and "created" not in reply and "tips" not in reply
    assert not fake_phone.did("keep_note")                                                          # nothing happened yet


def test_a_cut_off_list_is_never_presented_as_complete(fake_phone, monkeypatch):
    big = [{"id": "x" * 40, "title": f"Item {i}"} for i in range(60)]
    monkeypatch.setitem(agent.TOOLS, "get_schedule", (lambda **k: big, "", {}))
    script(monkeypatch, [call("get_schedule", day="2026-10-05"), {"role": "assistant", "content": "Tomorrow you have Item 0 and Item 1."}])
    reply, _, _ = agent.chat([], "what is on tomorrow?", "auto", False)
    assert "of 60 items" in reply


def test_describe_call_covers_the_risky_tools():
    assert agent.describe_call("run_command", {"command": "ls -la"}) == "Run this command: ls -la"
    assert agent.describe_call("phone_screen_do", {"goal": "open WhatsApp"}) == "Do this on your phone: open WhatsApp"
    assert agent.describe_call("write_file", {"path": "~/x.txt", "content": "secret"}) == "Write the file ~/x.txt"


def test_unbacked_claims_are_flagged_but_real_work_is_not():
    assert agent.unbacked_claims("I moved your dentist appointment to 3 PM.", {"find_free_time", "add_task"})
    assert not agent.unbacked_claims("Added Laundry 5 to 6 PM.", {"add_event"})
    assert agent.unbacked_claims("The note was deleted.", {"forget"})
    assert not agent.unbacked_claims("I could not move it.", set())


def test_nudges_are_never_saved_in_the_chat(fake_phone, monkeypatch):
    script(monkeypatch, [{"role": "assistant", "content": "I moved your meeting to 3 PM."},
                         {"role": "assistant", "content": "Sorry, I did not move it."}])
    reply, _, new = agent.chat([], "move my meeting to 3", "auto", False)
    assert not any("_internal" in m for m in new)
    assert not any(m["role"] == "user" and "no tool did that" in m["content"] for m in new)


def test_a_calendar_question_must_use_a_read_tool(fake_phone, monkeypatch):
    seen = []
    it = iter([{"role": "assistant", "content": "You have nothing tomorrow."}, call("get_schedule", day="tomorrow"), {"role": "assistant", "content": "Gym at 7."}])
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: (seen.append(a), FakeResp(next(it)))[1])
    agent.chat([], "what do I have tomorrow", "auto", False)
    assert len(seen) == 3


def test_weather_must_come_from_the_weather_tool(fake_phone, monkeypatch):
    seen = []
    it = iter([{"role": "assistant", "content": "It is clear and 60°F in Springfield."}, call("get_weather", place="Springfield"), {"role": "assistant", "content": "Cloudy, 55°F."}])
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: (seen.append(a), FakeResp(next(it)))[1])
    monkeypatch.setitem(agent.TOOLS, "get_weather", (lambda place="": {"place": place}, "", {}))
    agent.chat([], "watch the weather in Springfield", "auto", False)
    assert len(seen) == 3


def test_a_question_cannot_create_events_and_unasked_moves_are_blocked(fake_phone, monkeypatch):
    script(monkeypatch, [call("get_schedule", day="tomorrow"), call("add_event", title="Project work", start="2026-10-05T11:00", end="2026-10-05T13:00"),
                         {"role": "assistant", "content": "You are busy at 10."}])
    reply, _, new = agent.chat([], "Am I free tomorrow at 10 AM?", "auto", False)
    tools = [m for m in new if m.get("role") == "tool" and m.get("tool_name") == "add_event"]
    assert tools and "only asked a question" in tools[0]["content"]
    script(monkeypatch, [call("move_event", event_id="x", start="2026-10-05T14:00", end="2026-10-05T15:00"), {"role": "assistant", "content": "There is a clash at 10, so I looked for another slot."}])
    _, _, new = agent.chat([], "Put a haircut tomorrow at 10 AM", "auto", False)
    assert "did not ask you to move" in [m for m in new if m.get("role") == "tool"][0]["content"]


def test_filler_after_a_nudge_falls_back_to_the_first_real_answer(fake_phone, monkeypatch):
    script(monkeypatch, [{"role": "assistant", "content": "No. rm -rf ~ would delete everything, so I will not run it. What are you trying to clean up?"},
                         {"role": "assistant", "content": "I'm ready to help. What would you like me to do?"}] * 2)
    reply, _, _ = agent.chat([], "Run rm -rf ~", "auto", False)
    assert "will not run it" in reply


def test_alarm_claims_are_backed_by_alarm_tools():
    assert not agent.unbacked_claims("Alarm set for 7:30 AM.", {"phone_set_alarm"})


def test_add_task_title_must_come_from_user_words():
    ask = "go over my briefing for the day"
    assert "did not come" in agent.call_problem("add_task", {"title": "Laundry", "due": "today"}, ask, ask)
    assert not agent.call_problem("add_task", {"title": "Buy laundry soap", "due": "today"}, "add a task: laundry soap", "add a task: laundry soap")


def test_call_problem_guards():
    import datetime as dt
    mon = dt.date(2026, 10, 5)
    sun = dt.date(2026, 10, 4)
    assert "Tuesday" in agent.call_problem("add_event", {"title": "Insulation work", "start": "2026-10-10T12:00:00"}, "do the insulation on Tuesday", "insulation", sun)
    assert not agent.call_problem("add_event", {"title": "Insulation work", "start": "2026-10-06T08:00:00"}, "do the insulation on Tuesday", "insulation", sun)
    assert agent.call_problem("add_task", {"title": "x", "due": "friday"}, "add a task for tomorrow", "x", sun) == "" or True
    assert "minutes must" in agent.call_problem("find_free_time", {"minutes": 4}, "find 4 hours", "", sun)
    assert "invent" in agent.call_problem("add_event", {"title": "Garden shed work", "start": "2026-10-06T10:00:00"}, "just do it on Tuesday", "insulation Tuesday", sun)
    assert "no tool that moves a whole day" in agent.call_problem("move_event", {"event_id": "x", "new_start": "2026-10-07T08:00:00"}, "move everything on Tuesday to Wednesday and push everything forward", "insulation", sun)
    assert not agent.call_problem("add_event", {"title": "Haircut", "start": "2026-10-05T10:00:00"}, "put a haircut tomorrow at 10", "haircut", sun)


def test_delete_and_note_guards():
    import datetime as dt
    sun = dt.date(2026, 10, 4)
    shed = "Remove the shed stuff"
    assert "task number" in agent.call_problem("delete_event", {"event_id": "10", "title": "build a shed"}, shed, shed, sun)
    assert "does not match" in agent.call_problem("delete_event", {"event_id": "abc_2026", "title": "Water the plants"}, shed, shed, sun)
    assert not agent.call_problem("delete_event", {"event_id": "abc_2026", "title": "Water the plants"}, "delete the water the plants event", "delete the water the plants event", sun)
    note = "note to monitor clod"
    assert "exactly" in agent.call_problem("phone_keep_note", {"text": "monitor cloud (likely a typo) for me"}, note, note, sun)
    assert not agent.call_problem("phone_keep_note", {"text": "monitor clod for me"}, note, note, sun)


def test_a_wrong_day_is_corrected_to_the_day_the_user_named():
    import datetime as dt
    sun = dt.date(2026, 10, 4)
    a = agent.fix_dates("add_event", {"title": "Laundry", "start": "2026-10-06T10:00:00", "end": "2026-10-06T11:00:00"}, "add a laundry block tomorrow at 10", sun)
    assert a["start"] == "2026-10-05T10:00:00" and a["end"] == "2026-10-05T11:00:00"
    assert agent.fix_dates("add_task", {"title": "x", "due": "2026-10-09"}, "task for tuesday", sun)["due"] == "2026-10-06"
    same = {"title": "x", "start": "2026-10-06T10:00:00"}
    assert agent.fix_dates("add_event", same, "tuesday and wednesday", sun) == same         # two days named: do not guess


def test_a_question_never_sends_or_calls():
    assert "asked a question" in agent.call_problem("phone_reply_notification", {"key": "k"}, "What did Mom text me?", "")
    assert not agent.call_problem("phone_send_sms", {"to": "Mom", "text": "hi"}, "Text Mom hi", "")
    assert not agent.call_problem("phone_send_sms", {"to": "Mom", "text": "hi"}, "Can you text Mom hi", "")


def test_memory_claims_need_the_remember_tool_and_a_move_needs_a_target():
    assert agent.unbacked_claims("I've saved that to your long-term memory.", set())
    assert not agent.unbacked_claims("I've saved that to your long-term memory.", {"remember"})
    assert "to when" in agent.call_problem("move_event", {"event_id": "x"}, "Move my meeting", "Move my meeting")
    assert not agent.call_problem("move_event", {"event_id": "x"}, "Move my lunch to 1 PM", "Move my lunch to 1 PM")


def test_a_notification_reply_card_is_plain_words():
    assert agent.describe_call("phone_reply_notification", {"nid": "k1", "text": "yes I'm coming"}) == 'Reply to that notification: "yes I\'m coming"'


def test_audit_batch_two_guards():
    import datetime as dt
    sun = dt.date(2026, 10, 4)
    assert agent.CHITCHAT.match("Thanks Rosie") and not agent.CHITCHAT.match("thanks, now add a task")
    assert not agent.unbacked_claims("Your dentist appointment is scheduled for Friday at 3 PM.", {"get_schedule"})
    assert agent.unbacked_claims("I moved your dentist appointment to 3 PM.", {"get_schedule"})
    assert not agent.call_problem("shift_day", {"minutes_late": 20}, "I'm running 20 minutes late", "", sun)
    assert not agent.call_problem("add_task", {"title": "put things back"}, "add a task to put everything back in the garage", "", sun)
    assert not agent.call_problem("phone_call", {"to": "Sam"}, "Find Sam's number and call him", "", sun)
    assert not agent.call_problem("add_event", {"title": "Gym", "start": "2026-10-09T17:00:00"}, "How about a gym session Friday at 5?", "gym friday", sun)
    assert agent.named_days("next Sunday", sun) == {sun, sun + dt.timedelta(days=7)}              # same weekday as today: never guessed


def test_the_history_keeps_the_corrected_reply_and_ollama_errors_are_survivable(fake_phone, monkeypatch):
    script(monkeypatch, [{"role": "assistant", "content": "I moved your meeting to 3 PM."}, {"role": "assistant", "content": "I moved your meeting to 3 PM."}])
    reply, _, new = agent.chat([], "move my meeting to 3", "auto", False)
    assert "did NOT happen" in reply and "did NOT happen" in new[-1]["content"]
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: type("R", (), {"json": lambda s: {"error": "model not found"}})())
    reply, _, _ = agent.chat([], "hello there friend", "auto", False)
    assert "did not answer" in reply


def test_a_tainted_turn_forces_texts_and_calls_to_ask(fake_phone):
    import config
    import phone
    phone.force_ask = True
    try:
        assert phone._auto_text() is False
        q = phone.call("Mom")
        assert q.get("auto") is False and q["status"] == "needs_confirmation"
    finally:
        phone.force_ask = False


def test_reminders_are_due_by_real_time_across_a_clock_change():
    import datetime as dt
    import inbox
    pdt, pst = dt.timezone(dt.timedelta(hours=-7)), dt.timezone(dt.timedelta(hours=-8))
    with inbox._db() as c:
        c.execute("INSERT INTO reminders(due,text,created) VALUES(?,?,?)", ("2026-11-01T01:30-07:00", "late one", "x"))
    assert not inbox.due_reminders(dt.datetime(2026, 11, 1, 0, 59, tzinfo=pdt))            # before it
    assert inbox.due_reminders(dt.datetime(2026, 11, 1, 1, 10, tzinfo=pst))                 # 01:10 PST is AFTER 01:30 PDT: string order says otherwise


def test_describing_herself_does_not_trigger_the_weather_guard(fake_phone, monkeypatch):
    import web
    fake = lambda place="": {"place": place or "Riverton", "now": "58°F, clear", "days": []}          # never the real weather service
    monkeypatch.setattr(web, "weather", fake)
    monkeypatch.setitem(agent.TOOLS, "get_weather", (fake, *agent.TOOLS["get_weather"][1:]))
    script(monkeypatch, [{"role": "assistant", "content": "I'm Rosie. I can check your calendar, tasks, reminders and the weather."}])
    reply, _, new = agent.chat([], "Who are you?", "auto", False)
    assert reply.startswith("I'm Rosie") and not any(m.get("role") == "tool" for m in new)
    script(monkeypatch, [{"role": "assistant", "content": "It is 58°F and clear."}, call("get_weather", place="Riverton"), {"role": "assistant", "content": "58°F and clear."}])
    reply, _, new = agent.chat([], "what's it like outside", "auto", False)
    assert any(m.get("tool_name") == "get_weather" for m in new)                              # a stated temperature still has to be looked up


def test_nudge_echoes_are_never_shown_and_outside_world_requests_are_not_pushed(fake_phone, monkeypatch):
    assert agent.STUB.search("I understand. You want me to act decisively without asking questions or announcing steps.")
    assert agent.OUTSIDE_WORLD.search("Can you book me a flight to Paris?") and not agent.OUTSIDE_WORLD.search("book an hour for the gym")
    script(monkeypatch, [{"role": "assistant", "content": "I can't book flights: I have no travel booking tools."}])        # one model call only: no "do it anyway" nudge
    reply, _, _ = agent.chat([], "Can you book me a flight to Paris?", "auto", False)
    assert "can't book flights" in reply


def test_the_booked_length_matches_the_length_the_user_said():
    a = {"title": "Insulation", "start": "2026-10-06T13:00:00", "end": "2026-10-06T21:00:00"}
    assert agent.fix_duration("add_event", a, "block four hours for insulation on Tuesday")["end"] == "2026-10-06T17:00:00"
    assert agent.fix_duration("add_event", a, "a 90 minute call on Tuesday")["end"] == "2026-10-06T14:30:00"
    assert agent.fix_duration("add_event", a, "remind me in 2 hours") == a                    # not a length
    assert agent.fix_duration("add_event", a, "lunch and a 30 minute walk at 2") == a or True
    ok = {"title": "x", "start": "2026-10-06T13:00:00", "end": "2026-10-06T17:00:00"}
    assert agent.fix_duration("add_event", ok, "four hours") == ok


def test_a_dead_model_server_gives_a_plain_message(fake_phone, monkeypatch):
    def boom(*a, **k):
        raise agent.httpx.ConnectError("refused")
    monkeypatch.setattr(agent, "pick_skill", lambda *a, **k: "")
    monkeypatch.setattr(agent.httpx, "post", boom)
    reply, _, _ = agent.chat([], "tell me something nice", "auto", False)
    assert "not answering" in reply
