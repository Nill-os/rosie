import agent


def run(text, pending=None, bad=None, used=(), stalled=0, announces=False, first_text="", cut=None):
    return agent.finalize_reply(text, pending or [], bad or [], used, stalled, announces, first_text, cut)


def test_plain_text_passes_through():
    assert run("Your meeting is at 3pm.") == "Your meeting is at 3pm."


def test_stalled_empty_text_gets_failure_note():
    out = run("", stalled=2)
    assert out.startswith("I did not get that done")


def test_stalled_announcing_text_keeps_original_and_appends_note():
    out = run("I'll do that now.", stalled=2, announces=True)
    assert out.startswith("I'll do that now.\n\nI did not get that done")


def test_not_stalled_enough_leaves_text():
    assert run("", stalled=1) == ""


def test_stub_restores_first_text():
    stub = "I'm ready to help. What would you like me to do?"
    assert run(stub, first_text="Here is the real answer.") == "Here is the real answer."


def test_stub_without_first_text_stays():
    stub = "What would you like me to do?"
    assert run(stub) == stub


def test_pending_ready_text_replaces_reply():
    pending = [{"summary": "Add dentist on Friday."}, {"summary": "Move gym"}]
    assert run("anything", pending=pending, stalled=2) == "Ready: Add dentist on Friday; Move gym."


def test_cut_off_note_added():
    cut = {"total_results": 40, "results_shown": 10}
    out = run("Here are some items.", cut=cut)
    assert out.endswith("(Only 10 of 40 items fit, so this list is incomplete. Ask me to narrow it down.)")


def test_cut_off_note_skipped_when_total_mentioned():
    cut = {"total_results": 40, "results_shown": 10}
    assert run("There are 40 items.", cut=cut) == "There are 40 items."


def test_unbacked_claim_rewritten_with_used_writes():
    out = run("I added it.", bad=["I added it."], used={"add_event", "get_schedule"})
    assert "did NOT happen" in out
    assert "What I really did this time: add_event." in out


def test_unbacked_claim_with_no_writes_says_nothing():
    assert "What I really did this time: nothing." in run("I moved it.", bad=["I moved it."], used=set())
