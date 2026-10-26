import agent


def test_trailing_offers_are_removed():
    assert agent.deoffer("Added laundry at 5 PM. Would you like me to add meal prep as well?") == "Added laundry at 5 PM."
    assert agent.deoffer("You have 3 tasks. Let me know if you want anything else.") == "You have 3 tasks."
    assert agent.deoffer("Done. Anything else I can help with?") == "Done."


def test_a_lone_question_is_kept():
    assert agent.deoffer("Which one: Sam Lee or Sam Ortiz?") == "Which one: Sam Lee or Sam Ortiz?"


def test_refusal_detector():
    assert agent.REFUSAL.search("I can't directly check your GPU.")
    assert agent.REFUSAL.search("You'll need to open Task Manager.")
    assert not agent.REFUSAL.search("Your GPU is at 48 degrees.")


def test_every_phone_tool_is_registered_and_grouped():
    names = [n for n in agent.TOOLS if n.startswith("phone_")]
    assert len(names) >= 14 and all(agent.GROUPS[n] in ("phone", "internal") for n in names)
    assert agent.GROUPS["phone_screen_tap"] == "internal"  # board #844 safety-screen-tap-confirm


def test_confirming_tools_are_write_tools():
    for n in ("phone_send_sms", "phone_call", "mail_cleanup", "add_event", "move_event"):
        assert n in agent.WRITE_TOOLS


def test_system_prompt_says_act_dont_ask():
    sp = agent.system_prompt("auto", False)
    assert "ACT, DON'T ASK" in sp and "Rosie" in sp and "Collaborate" not in sp
