# board #839 owner-claude-plans-verifies
from pathlib import Path

import a0
import agent
import claude_plan

FAKE_KEY = "sk-" + "abcdefghijklmnop123456"


def test_agent_zero_gets_the_plan_and_verify_rules_in_its_own_prompt_file(tmp_path, monkeypatch):
    for name in ("INCLUDE", "INCLUDE_TOOLS", "INCLUDE_CLAUDE"):
        monkeypatch.setattr(a0, name, tmp_path / getattr(a0, name).name)
    monkeypatch.setattr(a0, "include_text", lambda: "rules")
    monkeypatch.setattr(a0, "tools_text", lambda: "tools")
    a0.write_include()
    text = (tmp_path / "claude.promptinclude.md").read_text(encoding="utf-8")
    assert "plan_with_claude" in text and "ask_claude_to_solve" in text and "/host/home" in text and "until it passes" in text
    assert "sort -h | head` shows the SMALLEST" in text and "never / " in text
    assert len(text) < 7000


def test_verify_with_claude_answers_ok_problems_and_fix(monkeypatch):
    seen = {}

    def fake_ask(prompt, schema=None, timeout=0, system=None):
        seen.update(prompt=prompt, schema=schema, system=system)
        return {"ok": False, "problems": ["The sizes are from /a0/usr/workdir, not /host/home."], "fix": "Run du on /host/home."}
    monkeypatch.setattr(claude_plan, "enabled", lambda: True)
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "ask", fake_ask)
    r = claude_plan.tool_verify("audit my PC", "I scanned the system", "64M /a0/usr/workdir/harness2\napi_key=" + FAKE_KEY)
    assert r["ok"] is False and "fix what it flags" in r["next"] and r["problems"]
    assert seen["system"] == claude_plan.VERIFY_RULES and seen["schema"] == claude_plan.VERIFY_SCHEMA
    assert FAKE_KEY not in seen["prompt"]  # board #839 safety-keys-stay-local


def test_verify_says_so_when_claude_cannot_be_used(monkeypatch):
    monkeypatch.setattr(claude_plan, "enabled", lambda: True)
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "_local", lambda *a: None)
    r = claude_plan.tool_verify("audit my PC", "done")
    assert "error" in r and "check the work yourself" in r["error"]


def test_both_tools_are_rosie_tools_in_the_plan_group_and_count_as_outside_text():
    assert "verify_with_claude" in agent.TOOLS and agent.GROUPS["verify_with_claude"] == "plan"
    assert "details" in agent.TOOLS["plan_with_claude"][2]
    assert {"plan_with_claude", "verify_with_claude"} <= agent.UNTRUSTED


def test_agent_deck_sections_can_be_reordered_and_the_board_is_back():
    deck = Path(__file__).resolve().parent.parent / "static" / "deck"
    js, html = (deck / "deck.js").read_text(encoding="utf-8"), (deck / "index.html").read_text(encoding="utf-8")
    assert 'data-sec="board"' in html and "drawMiniBoard" in js
    assert "SET.order" in js and "draggable: 'true'" in js and "moveSection" in js


import asyncio
import importlib.util
import json
import sys
import types

import pytest

import a0_mcp

EXT = Path(a0.__file__).parent / "agent_zero_ext" / "tool_execute_before" / "_30_claude_check.py"


class Repairable(Exception):
    pass


@pytest.fixture
def ext(monkeypatch):
    errors = types.ModuleType("helpers.errors")
    errors.RepairableException = Repairable
    extension = types.ModuleType("helpers.extension")
    extension.Extension = type("Extension", (), {"__init__": lambda self, agent=None: setattr(self, "agent", agent)})
    helpers = types.ModuleType("helpers")
    for name, mod in (("helpers", helpers), ("helpers.errors", errors), ("helpers.extension", extension)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("claude_check_ext", EXT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def item(type, content="", heading="", kvps=None):
    return types.SimpleNamespace(type=type, content=content, heading=heading, kvps=kvps)


class Shown:
    def __init__(self):
        self.content = None

    def update(self, content=None, **kw):
        self.content = content


def a0_agent(logs, number=0, iteration=1):
    loop = types.SimpleNamespace(iteration=iteration, params_persistent={}, params_temporary={"log_item_response": Shown()})
    ag = types.SimpleNamespace(number=number, loop_data=loop, context=types.SimpleNamespace(log=types.SimpleNamespace(logs=logs)), warnings=[])
    ag.hist_add_warning = ag.warnings.append
    return ag


DISK = [item("user", "audit my disk"), item("agent", "A0: scanning"),
        item("code_exe", "107G\t/host/home/Pictures\n77G\t/host/home/Desktop", "du"), item("agent", "A0: answering")]


def routes(monkeypatch, ext, **answers):
    calls = []

    def post(route, payload):
        calls.append((route, payload))
        a = answers[route.strip("/")]
        return a(payload) if callable(a) else a
    monkeypatch.setattr(ext, "_post", post)
    return calls


def run(ext, agent, text="Pictures is 8 GB. Delete Models, it is safe.", tool="response"):
    args = {"text": text}
    asyncio.run(ext.ClaudeLoop(agent).execute(tool_name=tool, tool_args=args))
    return args["text"]


def test_a_wrong_answer_goes_back_to_agent_zero_with_claudes_problems(ext, monkeypatch):
    calls = routes(monkeypatch, ext, check={"ok": False, "problems": ["Pictures is 107G, not 8 GB."], "fix": "Use du.", "by": "claude"})
    with pytest.raises(Repairable) as e:
        run(ext, a0_agent(DISK))
    assert "Pictures is 107G" in str(e.value) and "never delete" in str(e.value) and "check 1 of 5" in str(e.value)
    assert [c[0] for c in calls] == ["/check"]
    p = calls[0][1]
    assert p["request"] == "audit my disk" and "Delete Models" in p["answer"] and "107G\t/host/home/Pictures" in p["evidence"]


def test_after_a_second_miss_claude_takes_over_the_hard_part(ext, monkeypatch):
    calls = routes(monkeypatch, ext, check={"ok": False, "problems": ["The fix is not applied yet."], "fix": "Apply it.", "by": "claude"},
                   solve={"solution": "1. Run `pytest -x tests/test_a.py`.", "by": "claude"})
    ag = a0_agent(DISK)
    with pytest.raises(Repairable):
        run(ext, ag)
    with pytest.raises(Repairable) as e:
        run(ext, ag)
    assert "Claude took over the hard part. Carry out exactly these steps now:\n1. Run `pytest -x tests/test_a.py`." in str(e.value)
    route, payload = calls[-1]
    assert route == "/solve" and "The fix is not applied yet." in payload["stuck_on"] and "107G" in payload["found"]


def test_it_loops_until_the_check_passes(ext, monkeypatch):
    verdicts = iter([{"ok": False, "problems": ["Not done."], "fix": "", "by": "claude"}, {"ok": True, "problems": [], "fix": "", "by": "claude"}])
    routes(monkeypatch, ext, check=lambda p: next(verdicts))
    ag = a0_agent(DISK)
    with pytest.raises(Repairable):
        run(ext, ag)
    assert run(ext, ag, "Pictures is 107 GB.") == "Pictures is 107 GB.\n\n(Checked by Claude.)"


def test_after_the_last_check_the_answer_goes_out_marked_unverified(ext, monkeypatch):
    routes(monkeypatch, ext, check={"ok": False, "problems": ["Models is not safe to delete."], "fix": "", "by": "claude"}, solve={"skip": "x"})
    ag = a0_agent(DISK)
    for _ in range(ext.MAX_CHECKS - 1):
        with pytest.raises(Repairable):
            run(ext, ag)
    out = run(ext, ag)
    assert out.endswith("\n\n(Claude's check did not pass: Models is not safe to delete. Treat this answer as unverified.)")
    assert ag.loop_data.params_temporary["log_item_response"].content == out


def test_when_nothing_can_check_the_answer_still_goes_out_and_says_why(ext, monkeypatch):
    routes(monkeypatch, ext, check={"skip": "Paused until 9:00 PM: usage limit."})
    assert run(ext, a0_agent(DISK), "Done.").endswith("\n\n(Not checked: Paused until 9:00 PM: usage limit.)")

    def down(route, payload):
        raise OSError("refused")
    monkeypatch.setattr(ext, "_post", down)
    assert "(Not checked: Rosie could not be reached (OSError).)" in run(ext, a0_agent(DISK), "Done.")


def test_chat_spoken_turns_and_helpers_are_left_alone(ext, monkeypatch):
    monkeypatch.setattr(ext, "_post", lambda *a: pytest.fail("should not ask Rosie"))
    weather = [item("user", "what's the weather?"), item("mcp", '{"temp_f": 55}', kvps={"tool_name": "rosie.call", "tool": "get_weather"})]
    assert run(ext, a0_agent(weather), "55 degrees.") == "55 degrees."
    spoken = [item("user", "[Spoken turn: answer in 1 to 3 short spoken sentences]\nclean my disk")] + DISK[1:]
    assert run(ext, a0_agent(spoken, iteration=6), "Done.") == "Done."
    assert run(ext, a0_agent(DISK, number=1), "Done.") == "Done."
    assert run(ext, a0_agent(DISK, iteration=0), tool="code_execution_tool") == run.__defaults__[0]


def test_any_task_of_several_steps_gets_a_plan_once_and_is_checked(ext, monkeypatch):
    calls = routes(monkeypatch, ext, plan={"plan": "1. Find the failing test.\n2. Fix it.", "by": "claude"},
                   check={"ok": True, "problems": [], "fix": "", "by": "claude"})
    research = [item("user", "compare three Meshtastic radios for me"), item("mcp", "page 1", kvps={"tool": "web_search"}),
                item("mcp", "page 2", kvps={"tool": "web_fetch"})]
    ag = a0_agent(research, iteration=2)
    run(ext, ag, tool="web_fetch")
    run(ext, ag, tool="web_fetch")
    assert [c[0] for c in calls] == ["/plan"] and "page 2" in calls[0][1]["found"]
    assert ag.warnings[0].startswith("[Plan from Claude for this task. The owner already asked for it") and "2. Fix it." in ag.warnings[0]
    assert run(ext, ag, "Here is the comparison.").endswith("(Checked by Claude.)")


def test_no_plan_when_it_already_asked_claude_or_rosie_brought_one(ext, monkeypatch):
    monkeypatch.setattr(ext, "_post", lambda *a: pytest.fail("already planned"))
    asked = [item("user", "plan my week"), item("mcp", "{}", kvps={"tool": "plan_with_claude"})]
    run(ext, a0_agent(asked, iteration=4), tool="get_schedule")
    brought = [item("user", "[Plan from Claude for this request.]\n1. x\n\nplan my week")]
    run(ext, a0_agent(brought, iteration=4), tool="get_schedule")


def test_rosie_pc_tools_count_as_work(ext):
    assert ext.is_task([item("mcp", "", kvps={"tool_name": "rosie.call", "tool": "run_command"})], 0)
    assert not ext.is_task([item("mcp", "", kvps={"tool_name": "rosie.call", "tool": "list_tasks"})], 1)


def test_rosies_loop_routes_use_claude_and_say_why_when_nothing_can_help(monkeypatch):
    monkeypatch.setattr(claude_plan, "verify", lambda r, a, e: {"ok": True, "problems": [], "fix": "", "by": "claude", "got": [r, a, e]})
    assert a0_mcp.check({"request": "q", "answer": "a", "evidence": "e"})["got"] == ["q", "a", "e"]
    monkeypatch.setattr(claude_plan, "plan_for_agent", lambda r, f: {"plan": f"plan {r} {f}", "by": "claude"})
    assert a0_mcp.plan({"request": "q", "found": "f"})["plan"] == "plan q f"
    monkeypatch.setattr(claude_plan, "solve", lambda r, f, s: {"solution": f"{r} {f} {s}", "by": "claude"})
    assert a0_mcp.solve({"request": "q", "found": "f", "stuck_on": "s"})["solution"] == "q f s"
    why = "Claude is paused until 9:00 PM: Your Claude usage limit is reached."
    monkeypatch.setattr(claude_plan, "why_not", lambda: why)
    for name in ("verify", "plan_for_agent", "solve"):
        monkeypatch.setattr(claude_plan, name, lambda *a: None)
    assert a0_mcp.check({}) == {"skip": why + " Rosie's own check could not run either."}
    assert a0_mcp.plan({}) == a0_mcp.solve({}) == {"skip": why}


def _asgi(app, path, body=b"", auth=b"Bearer tok"):
    sent, msgs = [], [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return msgs.pop(0)

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "path": path, "method": "POST", "headers": [(b"authorization", auth)] if auth else []}
    asyncio.run(app(scope, receive, send))
    return sent


def test_the_loop_routes_need_the_token_and_never_reach_the_tool_server(monkeypatch):
    for path in ("/check", "/plan", "/solve", "/write"):
        monkeypatch.setitem(a0_mcp.LOOP_ROUTES, path, lambda req, path=path: {"route": path, "got": req})

    async def tools_app(scope, receive, send):
        pytest.fail("a loop route must not go to the MCP app")
    app = a0_mcp._Bearer(tools_app, "tok")
    assert _asgi(app, "/check", b"{}", auth=b"Bearer wrong")[0]["status"] == 401
    for path in ("/check", "/plan", "/solve", "/write"):
        sent = _asgi(app, path, json.dumps({"request": "r"}).encode())
        assert sent[0]["status"] == 200 and json.loads(sent[1]["body"]) == {"route": path, "got": {"request": "r"}}
    assert json.loads(_asgi(app, "/check", b"not json")[1]["body"])["got"] == {}
    assert json.loads(_asgi(app, "/plan", b"[1, 2]")[1]["body"])["got"] == {}


def test_rosie_installs_the_loop_into_agent_zero_and_keeps_it_current(tmp_path, monkeypatch):
    ext_dir = tmp_path / "usr" / "extensions" / "python"
    ext_dir.mkdir(parents=True)
    monkeypatch.setattr(a0, "EXT_DIR", ext_dir)
    a0.install_extensions()
    dst = ext_dir / "tool_execute_before" / "_30_claude_check.py"
    assert dst.read_text(encoding="utf-8") == EXT.read_text(encoding="utf-8")
    dst.write_text("old")
    a0.install_extensions()
    assert dst.read_text(encoding="utf-8") == EXT.read_text(encoding="utf-8")


# board #839 owner-token-fallback-handler
WRONG = """| 4 | `/host/home/.local/lib` | **5.3G** | 🟢 Safe to delete |
| 8 | `/host/home/Desktop` | **8.0G** | 🟡 Ask the owner |
| 9 | `/host/home/project-data/.git/objects/pack` | **9.1G** | 🟢 Safe to delete |
| 12 | `/host/home/.cache/huggingface/hub/...` | **16.7G** | 🟢 Safe to delete |
| 14 | `/host/home/Archive` | **2.1GB** | Ask the owner |
find /host/home -type f -mtime +90 -exec rm -f {} +"""
MEASURED = "107G\t/host/home/Pictures\n77G\t/host/home/Desktop\n5.3G\t/host/home/.local/lib\n13G\t/host/home/.cache/uv\n26G\t/host/home/project-data"


def test_rosies_code_catches_wrong_sizes_keep_folders_and_broad_deletes():
    problems, hints = claude_plan.local_findings(WRONG, MEASURED)
    text = "\n".join(problems)
    assert "It says /host/home/Desktop is 8.0G, but the command output shows 77.0G." in problems
    assert "/host/home/.local/lib safe to delete" in text and ".git/objects/pack safe to delete" in text
    assert "find /host/home -type f -mtime +90 -exec rm" in text
    assert "/host/home/Archive (2.1GB) is not in the command output given." in hints
    assert "16.7" not in text
    good = "| `/host/home/Pictures` | 107G | Ask the owner |\n| `/host/home/.cache/uv` | 13G | Safe to delete |\nRun `uv cache clean`."
    assert claude_plan.local_findings(good, MEASURED) == ([], [])
    assert claude_plan.local_findings("rm -rf ~/.cache/huggingface/hub/models--x", "") == ([], [])


def test_without_claude_rosie_checks_with_her_own_model_and_her_code_wins(monkeypatch):
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "why_not", lambda: "Claude is paused until 9:00 PM: Your Claude usage limit is reached.")
    seen = {}

    def local(system, user, schema=None):
        seen.update(system=system, user=user, schema=schema)
        return {"ok": True, "problems": [], "fix": ""}
    monkeypatch.setattr(claude_plan, "_local", local)
    v = claude_plan.verify("clean my disk", WRONG, MEASURED)
    assert v["by"] == "rosie" and v["ok"] is False and "usage limit" in v["why"]
    assert any("Desktop is 8.0G" in p for p in v["problems"]) and "Rosie's code found" in seen["user"]
    assert seen["schema"] == claude_plan.VERIFY_SCHEMA and "standing in for Claude" in seen["system"]
    v = claude_plan.verify("clean my disk", "Pictures is 107G (`/host/home/Pictures`).", MEASURED)
    assert v["ok"] is True and v["by"] == "rosie"
    r = claude_plan.tool_verify("clean my disk", "done", MEASURED)
    assert "Verified by Rosie's local check (Claude can't be used now)" in r["next"]


def test_without_claude_or_rosies_model_only_code_problems_count(monkeypatch):
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "_local", lambda *a: None)
    assert claude_plan.verify("q", "All fine.", MEASURED) is None
    v = claude_plan.verify("q", WRONG, MEASURED)
    assert v["ok"] is False and v["by"] == "rosie" and v["problems"]


def test_claude_is_tried_first_and_says_it_checked(monkeypatch):
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "ask", lambda *a, **k: {"ok": True, "problems": [], "fix": ""})
    monkeypatch.setattr(claude_plan, "_local", lambda *a: pytest.fail("Claude answered"))
    assert claude_plan.verify("q", "a", "e")["by"] == "claude"


def test_without_claude_agent_zero_gets_rosies_own_plan_marked_as_hers(monkeypatch):
    monkeypatch.setattr(claude_plan, "enabled", lambda: True)
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "context", lambda: "Next week: nothing fixed.")
    monkeypatch.setattr(claude_plan, "why_not", lambda: "Claude is paused until 9:00 PM: Your Claude usage limit is reached.")
    seen = {}
    monkeypatch.setattr(claude_plan, "_local", lambda system, user, schema=None: seen.update(system=system, user=user) or "1. Run du.")
    r = claude_plan.tool_plan("clean my disk", details="107G Pictures")
    assert r == {"plan": "1. Run du.", "by": "rosie", "note": "Claude is paused until 9:00 PM: Your Claude usage limit is reached. "
                 "This plan is from Rosie's local model: check it step by step as you go."}
    assert seen["system"] == claude_plan.RULES and "107G Pictures" in seen["user"]


def test_the_chat_note_says_which_checker_ran(ext):
    why = "Claude is paused until 9:00 PM: Your Claude usage limit is reached."
    assert ext.verdict_note({"ok": True, "by": "rosie", "why": why}) == f"(Checked by Rosie's local model, not Claude. {why})"
    assert ext.verdict_note({"ok": False, "by": "rosie", "why": why, "problems": ["Desktop is 77G."]}) == \
        f"(Rosie's local check did not pass: Desktop is 77G. Treat this answer as unverified. {why})"
    assert ext.verdict_note({"ok": True, "by": "claude"}) == "(Checked by Claude.)"


def test_without_claude_agent_zero_still_gets_a_plan_and_a_solution_from_rosie(monkeypatch):
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "context", lambda: "Next week: nothing fixed.")
    why = "Claude is paused until 9:00 PM: Your Claude usage limit is reached."
    monkeypatch.setattr(claude_plan, "why_not", lambda: why)
    seen = []
    monkeypatch.setattr(claude_plan, "_local", lambda system, user, schema=None: seen.append((system, user)) or "1. Do it.")
    assert claude_plan.plan_for_agent("fix the build", "error: x") == {"plan": "1. Do it.", "by": "rosie", "why": why}
    assert claude_plan.solve("fix the build", "error: x", "the linker") == {"solution": "1. Do it.", "by": "rosie", "why": why}
    assert seen[1][0] == claude_plan.SOLVE_RULES and "the linker" in seen[1][1] and "error: x" in seen[1][1]
    assert "Rosie's local model (Claude can't be used now)" in claude_plan.tool_solve("fix the build")["next"]
    monkeypatch.setattr(claude_plan, "_local", lambda *a: None)
    assert claude_plan.plan_for_agent("q") is None and claude_plan.solve("q") is None and "error" in claude_plan.tool_solve("q")


def test_with_claude_the_hard_part_and_the_plan_come_from_claude_without_secrets(monkeypatch):
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "context", lambda: "Next week: nothing fixed.")
    seen = []
    monkeypatch.setattr(claude_plan, "ask", lambda prompt, schema=None, timeout=0, system=None: seen.append((prompt, system)) or "1. Run make.")
    monkeypatch.setattr(claude_plan, "_local", lambda *a: pytest.fail("Claude answered"))
    assert claude_plan.solve("fix the build", "token=" + FAKE_KEY, "linker") == {"solution": "1. Run make.", "by": "claude"}
    assert claude_plan.plan_for_agent("fix the build", "found x") == {"plan": "1. Run make.", "by": "claude"}
    assert seen[0][1] == claude_plan.SOLVE_RULES and FAKE_KEY not in seen[0][0]
    assert seen[1][1] is None and "Present it" not in seen[1][0]


def test_agent_zero_can_ask_claude_to_solve_through_rosies_plan_group():
    assert agent.GROUPS["ask_claude_to_solve"] == "plan" and "ask_claude_to_solve" in agent.UNTRUSTED


def test_claude_sees_each_command_so_typed_in_output_is_not_evidence(ext, monkeypatch):
    fake = "print('Albums Count: 4')\nprint('Status: 200 OK')"
    calls = routes(monkeypatch, ext, check={"ok": True, "problems": [], "fix": "", "by": "claude"})
    logs = [item("user", "connect to my immich server"), item("code_exe", "Albums Count: 4\nStatus: 200 OK", "[0] output",
                                                              {"runtime": "python", "code": fake})]
    run(ext, a0_agent(logs), text="Immich is up: 200 OK, 4 albums.")
    assert f"$ {fake}\nAlbums Count: 4" in calls[0][1]["evidence"]
    assert "typed into its own command" in claude_plan.VERIFY_RULES


# board #839 owner-ask-other-ais
IMMICH = [item("user", "move my pictures to immich on my proxmox"), item("code_exe", "ls: Pictures Videos", "ls",
                                                                          {"code": "ls /host/home"})]
ASKS = "I need your Immich server address and API key."


def test_what_an_answer_asks_the_owner_goes_to_the_other_ais_first_once_a_turn(ext, monkeypatch):
    team = [{"question": "What is the Immich address?", "known": True, "answer": "10.0.4.52:2283", "source": "Claude Code memory",
             "how_to_find": ""},
            {"question": "What is the Immich API key?", "known": False, "answer": "", "source": "", "how_to_find": "ask the owner"}]
    calls = routes(monkeypatch, ext, check=lambda p: {"ok": True, "problems": [], "fix": "", "by": "claude",
                                                      **({} if p["asked"] else {"team": team})})
    ag = a0_agent(IMMICH)
    with pytest.raises(Repairable) as e:
        run(ext, ag, text=ASKS)
    msg = str(e.value)
    assert "10.0.4.52:2283" in msg and "other AIs" in msg and "confirm it with a real command" in msg
    assert "API key" not in msg and "NOT done" not in msg
    assert run(ext, ag, text=ASKS).startswith(ASKS)
    assert [c[1]["asked"] for c in calls] == [False, True]


def test_when_nobody_knows_it_says_how_to_find_it_and_owner_only_facts_go_to_the_owner(ext, monkeypatch):
    team = [{"question": "Immich address?", "known": False, "answer": "", "source": "",
             "how_to_find": "nmap -p 2283 --open 10.0.4.0/24, then curl http://<ip>:2283/api/server/ping"}]
    routes(monkeypatch, ext, check={"ok": True, "problems": [], "fix": "", "by": "claude", "team": team})
    with pytest.raises(Repairable) as e:
        run(ext, a0_agent(IMMICH), text=ASKS)
    assert "nobody knew; find it out: nmap -p 2283" in str(e.value)
    only_owner = [{"question": "API key?", "known": False, "answer": "", "source": "", "how_to_find": "Ask the owner."}]
    routes(monkeypatch, ext, check={"ok": True, "problems": [], "fix": "", "by": "claude", "team": only_owner})
    assert run(ext, a0_agent(IMMICH), text=ASKS).startswith(ASKS)


def step(tool, sub=""):
    args = f'{{"tool": "{sub}", "args": {{}}}}' if sub else "{}"
    return item("agent", f'{{"thoughts": ["x"], "headline": "h", "tool_name": "{tool}", "tool_args": {args}}}', "A0: step")


def test_a_tool_that_keeps_returning_the_same_thing_is_not_called_again(ext, monkeypatch):
    routes(monkeypatch, ext)
    nope = '{"status": "not_done", "message": "This needs the owner\'s Yes"}'
    logs = [item("user", "move my pictures")]
    for _ in range(3):
        logs += [step("rosie.call", "run_command"), item("mcp", nope, "A0: Using MCP tool 'rosie.call'", {"tool": "run_command"})]
    logs.append(step("rosie.call", "run_command"))
    with pytest.raises(Repairable) as e:
        asyncio.run(ext.ClaudeLoop(a0_agent(logs, iteration=1)).execute(tool_name="rosie.call", tool_args={"tool": "run_command"}))
    assert "same thing" in str(e.value) and "not_done" in str(e.value) and "own terminal" in str(e.value)
    asyncio.run(ext.ClaudeLoop(a0_agent(logs, iteration=1)).execute(tool_name="rosie.call", tool_args={"tool": "list_dir"}))
    mixed = logs[:3] + [step("rosie.call", "run_command"), item("mcp", "other", "", {"tool": "run_command"})] + logs[3:]
    asyncio.run(ext.ClaudeLoop(a0_agent(mixed[:-1], iteration=1)).execute(tool_name="rosie.call", tool_args={"tool": "run_command"}))


def test_its_own_tools_failing_the_same_way_count_too_but_its_terminal_does_not(ext, monkeypatch):
    routes(monkeypatch, ext)
    err = ("Traceback (most recent call last):\n  File \"/a0/agent.py\", line 538\nhelpers.errors.RepairableException: `context_id` "
           "continues an existing subordinate and requires reset=false.")
    logs = [item("user", "questions for meshtastic features")]
    for _ in range(3):
        logs += [step("call_subordinate"), item("subagent", ""), item("warning", err)]
    with pytest.raises(Repairable) as e:
        asyncio.run(ext.ClaudeLoop(a0_agent(logs, iteration=1)).execute(tool_name="call_subordinate", tool_args={"message": "go"}))
    assert "requires reset=false" in str(e.value)
    blocked = logs + [step("call_subordinate"), item("warning", "Traceback...\nRepairableException: " + str(e.value))]
    assert ext.results(blocked)[-1][1].endswith("reset=false.")
    idle = "[SYSTEM: Returning control to agent after 120 seconds with no output.]"
    scans = [item("user", "scan my disk")] + [x for _ in range(4) for x in (step("code_execution_tool"), item("code_exe", idle))]
    asyncio.run(ext.ClaudeLoop(a0_agent(scans, iteration=1)).execute(tool_name="code_execution_tool", tool_args={"code": "x"}))


def test_reading_the_same_unchanged_file_again_and_again_is_stopped(ext, monkeypatch):
    routes(monkeypatch, ext)
    read = "/a0/usr/workdir/jobs/mesh/questions.md 165 lines\n>>>\n  1 # Meshtastic Handheld Interview Questions"
    logs = [item("user", "fix these questions")]
    for _ in range(3):
        logs += [step("text_editor"), item("text_editor", read, "Reading questions.md", {"action": "read", "_tool_name": "text_editor"})]
    with pytest.raises(Repairable) as e:
        asyncio.run(ext.ClaudeLoop(a0_agent(logs, iteration=1)).execute(tool_name="text_editor", tool_args={"action": "read"}))
    assert "165 lines" in str(e.value)
    edited = logs[:-2] + [step("text_editor"), item("text_editor", "questions.md written", "Writing")] + logs[-2:]
    asyncio.run(ext.ClaudeLoop(a0_agent(edited, iteration=1)).execute(tool_name="text_editor", tool_args={"action": "read"}))

def test_a_one_step_answer_to_a_real_request_is_checked_but_small_talk_is_not(ext, monkeypatch):
    calls = routes(monkeypatch, ext, check={"ok": False, "problems": ["It greets the owner and never lists any questions."],
                                            "fix": "Write the questions.", "by": "claude"})
    ask = ("[What you remember: Sam has a project involving Meshtastic.]\n\nlayout potential questions we can ask people for "
           "features to add to the meshtastic bases on bill aulet 24 step theory")
    with pytest.raises(Repairable):
        run(ext, a0_agent([item("user", ask)], iteration=0), "Hello again, Sam! What's on your mind right now?")
    assert len(calls) == 1
    long_context = "[What you remember: " + "word " * 50 + "]\n\nhi there"
    assert run(ext, a0_agent([item("user", long_context)], iteration=0), "Hi!") == "Hi!"


def test_the_other_ais_lines_come_from_every_source_masked_and_claude_answers_from_them(tmp_path, monkeypatch):
    mem = tmp_path / "proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "homelab.md").write_text("Proxmox host 10.0.4.10, Immich LXC 10.0.4.52:2283\napi_key=" + FAKE_KEY + " immich\nunrelated\n")
    monkeypatch.setattr(claude_plan, "CLAUDE_MEMORY", tmp_path)
    stubs = {"mind": types.SimpleNamespace(recall=lambda q, k=10: [{"text": "Owner runs Immich on Proxmox", "when": "2026-10-01"},
                                                                    {"text": "Laundry", "when": "2026-10-02"}]),
             "agentboard_graph": types.SimpleNamespace(history=lambda q: {"posts": []}),
             "vaultsearch": types.SimpleNamespace(search=lambda w, n: []),
             "chats": types.SimpleNamespace(search=lambda w, n: [])}
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    lines = claude_plan.team_notes(["What is the IP address of the owner's Proxmox Immich server?"])
    assert any("10.0.4.52:2283" in x and "Claude Code memory homelab.md" in x for x in lines)
    assert any("Rosie's memory" in x for x in lines) and not any("Laundry" in x or "unrelated" in x for x in lines)
    seen = {}

    def fake_ask(prompt, schema=None, timeout=0, system=None):
        seen.update(prompt=prompt, schema=schema, system=system)
        return {"answers": [{"question": "Immich address?", "known": True, "answer": "10.0.4.52:2283", "source": "Claude Code memory",
                             "how_to_find": ""}]}
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "ask", fake_ask)
    t = claude_plan.tool_ask_team("What is the Immich server address?", "move my pictures to immich")
    assert t["answers"][0]["answer"] == "10.0.4.52:2283" and t["by"] == "claude" and "how_to_find" in t["next"]
    assert "10.0.4.52:2283" in seen["prompt"] and FAKE_KEY not in seen["prompt"]
    assert seen["system"] == claude_plan.TEAM_RULES and seen["schema"] == claude_plan.TEAM_SCHEMA


def test_rosies_check_route_asks_the_team_for_what_the_answer_asks_the_owner(monkeypatch):
    asked = []
    monkeypatch.setattr(claude_plan, "verify", lambda r, a, e: {"ok": True, "problems": [], "fix": "", "by": "claude",
                                                                "asks_owner": ["Immich address?"]})
    monkeypatch.setattr(claude_plan, "ask_team", lambda qs, req: asked.append(qs) or {"answers": [{"question": qs[0], "known": True}]})
    assert a0_mcp.check({"request": "r", "answer": ASKS})["team"][0]["known"] is True
    assert "team" not in a0_mcp.check({"request": "r", "answer": ASKS, "asked": True}) and asked == [["Immich address?"]]


def test_agent_zero_knows_to_ask_the_other_ais_and_to_use_its_own_terminal():
    assert "ask_other_ais" in agent.TOOLS and agent.GROUPS["ask_other_ais"] == "plan" and "ask_other_ais" in agent.UNTRUSTED
    assert "ask_other_ais" in a0.CLAUDE_RULES and "code_execution_tool" in a0.CLAUDE_RULES
    assert "asks_owner" in claude_plan.VERIFY_SCHEMA["required"]
    src = Path(a0_mcp.__file__).read_text(encoding="utf-8")
    assert "use your own terminal (code_execution_tool)" in src and "a0.write_include()" in src


def test_agent_zero_notes_which_chat_a_marked_rosie_message_landed_in(ext, tmp_path):
    spec = importlib.util.spec_from_file_location("rosie_turn_ext", EXT.parent.parent / "message_loop_start" / "_15_rosie_turn.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TURNS = str(tmp_path / "turns")
    ag = a0_agent([item("user", "[Rosie turn 0123456789abcdef]\n\nmove my photos"), item("agent", "thinking")])
    ag.context.id = "ctxA"
    asyncio.run(mod.RosieTurn(ag).execute())
    assert (tmp_path / "turns" / "0123456789abcdef").read_text(encoding="utf-8") == "ctxA"
    helper = a0_agent([item("user", "[Rosie turn fedcba9876543210]\n\nhi")], number=1)
    helper.context.id = "ctxB"
    asyncio.run(mod.RosieTurn(helper).execute())
    plain = a0_agent([item("user", "hello from the window")])
    plain.context.id = "ctxC"
    asyncio.run(mod.RosieTurn(plain).execute())
    assert [p.name for p in (tmp_path / "turns").iterdir()] == ["0123456789abcdef"]


# board #839 owner-claude-writes-text
def workdir(monkeypatch, ext, tmp_path):
    import re
    root = tmp_path / "workdir"
    (root / "jobs" / "mesh").mkdir(parents=True)
    monkeypatch.setattr(ext, "WORKDIR", str(root) + "/")
    monkeypatch.setattr(ext, "JOBS", str(root / "jobs") + "/")
    monkeypatch.setattr(ext, "WORK_PATH", re.compile(re.escape(str(root)) + r"/[\w./-]+"))
    return root


@pytest.mark.skipif(sys.platform == "win32", reason="Agent Zero's extension runs in its Linux container, with Linux paths")
def test_a_writing_job_that_misses_is_written_by_claude_saved_and_sent(ext, monkeypatch, tmp_path):
    root = workdir(monkeypatch, ext, tmp_path)
    job = root / "jobs" / "mesh"
    (job / "context.md").write_text("Trail Radio: 2 inch screen, 30-key keypad, 89-99 USD.")
    target = str(job / "questions.md")
    calls = routes(monkeypatch, ext, check={"ok": False, "problems": ["Questions are hypothetical."], "fix": "Ask about past behavior.",
                                            "asks_owner": [], "writing": True, "by": "claude"},
                   write=lambda req: {"text": "# Questions\n1. Tell me about the last time you typed off-grid. [keyboard]", "path": target,
                                      "by": "claude"})
    ask = f"[Work job from Claude. Your job folder: {job}]\n\nWrite interview questions about the handheld into questions.md."
    out = run(ext, a0_agent([item("user", ask), item("text_editor", "questions.md 165 lines >>> 1 # Old draft")], iteration=4),
              "# Old draft\n1. Would you buy it?")
    assert out.startswith("# Questions\n1. Tell me about the last time") and f"Written by Claude" in out and f"Saved to {target}" in out
    assert (job / "questions.md").read_text(encoding="utf-8").startswith("# Questions")
    sent = calls[-1][1]
    assert calls[-1][0] == "/write" and "30-key keypad" in sent["material"] and "Old draft" in sent["draft"]
    assert "hypothetical" in sent["problems"]


def test_claude_writes_once_a_turn_and_never_for_other_jobs(ext, monkeypatch, tmp_path):
    workdir(monkeypatch, ext, tmp_path)
    miss = {"ok": False, "problems": ["Pictures is 107G."], "fix": "Use du.", "asks_owner": [], "writing": False, "by": "claude"}
    calls = routes(monkeypatch, ext, check=miss, solve={"skip": "x"}, write=lambda req: pytest.fail("not a writing job"))
    with pytest.raises(Repairable):
        run(ext, a0_agent([item("user", "how big is my Pictures folder on my PC")], iteration=4))
    ag = a0_agent([item("user", "write the report about my disk use and save it as report.md")], iteration=4)
    ag.loop_data.params_persistent[ext.WROTE] = True
    routes(monkeypatch, ext, check={**miss, "writing": True}, solve={"skip": "x"}, write=lambda req: pytest.fail("twice"))
    with pytest.raises(Repairable):
        run(ext, ag)


@pytest.mark.skipif(sys.platform == "win32", reason="Agent Zero's extension runs in its Linux container, with Linux paths")
def test_claudes_text_is_saved_only_in_agent_zeros_own_job_files(ext, monkeypatch, tmp_path):
    root = workdir(monkeypatch, ext, tmp_path)
    (root / "notes.md").write_text("Agent Zero's own note")
    assert ext.save(str(root / "jobs" / "mesh" / "out.md"), "text") == str(root / "jobs" / "mesh" / "out.md")
    assert ext.save(str(root / "new.md"), "text") == str(root / "new.md")
    assert ext.save(str(root / "notes.md"), "text") == "" and (root / "notes.md").read_text(encoding="utf-8") == "Agent Zero's own note"
    assert ext.save(str(root / "rosie.promptinclude.md"), "x") == "" and ext.save("/host/home/notes.md", "x") == ""
    assert ext.save(str(root / "jobs" / ".." / ".." / "escape.md"), "x") == "" and ext.save(str(root / "run.sh"), "x") == ""
    assert "Not saved: /host/home/a.md" in ext.written_note({"by": "claude", "path": "/host/home/a.md"}, "")


def test_claude_writes_the_text_with_the_material_and_rosies_model_stands_in(monkeypatch):
    seen = {}

    def fake_ask(prompt, schema=None, timeout=0, system=None):
        seen.update(prompt=prompt, schema=schema, system=system)
        return {"text": "# Final", "path": "/a0/usr/workdir/jobs/x/q.md"}
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "ask", fake_ask)
    w = claude_plan.write("write questions", "spec: 40 keys api_key=" + FAKE_KEY, "draft", "too vague")
    assert w == {"text": "# Final", "path": "/a0/usr/workdir/jobs/x/q.md", "by": "claude"}
    assert seen["system"] == claude_plan.WRITE_RULES and seen["schema"] == claude_plan.WRITE_SCHEMA and "40 keys" in seen["prompt"]
    assert FAKE_KEY not in seen["prompt"]
    monkeypatch.setattr(claude_plan, "available", lambda: False)
    monkeypatch.setattr(claude_plan, "_local", lambda *a: {"text": "# Local", "path": ""})
    assert claude_plan.write("write questions")["by"] == "rosie"
    assert a0_mcp.write({"request": "r"})["text"] == "# Local"
    assert "writing" in claude_plan.VERIFY_SCHEMA["required"] and "WRITING JOBS" in a0.CLAUDE_RULES


def test_claude_sees_what_its_own_file_and_browser_tools_returned_but_not_its_thoughts(ext):
    items = [step("text_editor"), item("text_editor", "/a0/usr/workdir/jobs/w/q.md written 4 lines", "Writing q.md"),
             item("browser", "Page title: Walkie-talkie ranges"), item("util", "0 memories found"), item("agent", '{"thoughts": ["secret plan"]}')]
    found = ext.evidence(items)
    assert "q.md written 4 lines" in found and "Walkie-talkie ranges" in found
    assert "secret plan" not in found and "memories" not in found


NO_AGENT = ("---------------------------------------------------------------------------\nModuleNotFoundError                       "
            "Traceback (most recent call last)\nCell In[1], line 1\n----> 1 from agent import Agent\n\n"
            "ModuleNotFoundError: No module named 'agent'\n(venv) root@be63c360015e:/a0/usr/workdir#")


def test_its_terminal_failing_the_same_way_three_times_is_stopped_once_a_streak(ext, monkeypatch):
    routes(monkeypatch, ext)
    logs = [item("user", "from what you have learned in your corner, how could we improve yourself?")]
    for n in range(3):
        logs += [step("code_execution_tool"), item("code_exe", NO_AGENT.replace("line 1", f"line {n + 1}"))]
    with pytest.raises(Repairable) as e:
        asyncio.run(ext.ClaudeLoop(a0_agent(logs, iteration=1)).execute(tool_name="code_execution_tool", tool_args={"code": "x"}))
    assert "No module named 'agent'" in str(e.value) and "memory_load" in str(e.value)
    blocked = logs + [step("code_execution_tool"), item("warning", "Traceback...\nRepairableException: " + str(e.value))]
    asyncio.run(ext.ClaudeLoop(a0_agent(blocked, iteration=1)).execute(tool_name="code_execution_tool", tool_args={"code": "y"}))
    again = blocked + [item("code_exe", NO_AGENT)]
    with pytest.raises(Repairable):
        asyncio.run(ext.ClaudeLoop(a0_agent(again, iteration=1)).execute(tool_name="code_execution_tool", tool_args={"code": "z"}))
    mixed = logs[:-1] + [item("code_exe", "Traceback...\nFileNotFoundError: [Errno 2] No such file or directory: 'x.txt'")]
    asyncio.run(ext.ClaudeLoop(a0_agent(mixed, iteration=1)).execute(tool_name="code_execution_tool", tool_args={"code": "x"}))
    assert ext.code_error("SyntaxWarning: invalid escape sequence\nok") == ""
    assert ext.code_error("bash: foo: command not found") == "bash: foo: command not found"


QUOTED = Path(a0.__file__).parent / "agent_zero_ext" / "tool_execute_before" / "_05_quoted_secrets.py"


def test_an_answer_quoting_an_unknown_secret_placeholder_still_goes_out(monkeypatch):
    secrets = types.ModuleType("helpers.secrets")
    secrets.ALIAS_PATTERN = r"§§secret\(([A-Za-z_][A-Za-z0-9_]*)\)"
    secrets.get_secrets_manager = lambda ctx: types.SimpleNamespace(load_secrets=lambda: {"API_KEY_ANTHROPIC": "sk"})
    extension = types.ModuleType("helpers.extension")
    extension.Extension = type("Extension", (), {"__init__": lambda self, agent=None: setattr(self, "agent", agent)})
    for name, mod in (("helpers", types.ModuleType("helpers")), ("helpers.secrets", secrets), ("helpers.extension", extension)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("quoted_secrets_ext", QUOTED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    agent = types.SimpleNamespace(context=None)
    args = {"text": "It hunted for `§§secret(PROJECT_BOARD_ID)`; key §§secret(api_key_anthropic) stays."}
    asyncio.run(mod.QuotedSecrets(agent).execute(tool_name="response", tool_args=args))
    assert args["text"] == "It hunted for `secret(PROJECT_BOARD_ID)`; key §§secret(api_key_anthropic) stays."
    code = {"code": "echo §§secret(PROJECT_BOARD_ID)"}
    asyncio.run(mod.QuotedSecrets(agent).execute(tool_name="code_execution_tool", tool_args=code))
    assert code["code"] == "echo §§secret(PROJECT_BOARD_ID)"


def test_claude_names_only_agent_zeros_real_tools_and_agent_zero_asks_its_corner_first(monkeypatch):
    assert "corner_memory" in claude_plan.VERIFY_RULES and "corner_memory" in claude_plan.SOLVE_RULES
    assert "can't be imported" in claude_plan.SOLVE_RULES and "search_memory_facts" not in claude_plan.A0_TOOLS
    sent = []
    monkeypatch.setattr(claude_plan, "available", lambda: True)
    monkeypatch.setattr(claude_plan, "context", lambda: "")
    monkeypatch.setattr(claude_plan, "ask", lambda prompt, *a, **k: sent.append(prompt) or "1. Call corner_memory.")
    assert claude_plan.plan_for_agent("what did you learn in your corner?")["plan"] == "1. Call corner_memory."
    assert "rosie.call" in sent[0]
    assert "always first when the owner asks what you read, found or learned in your corner" in a0.RULES
    assert "never Python imports" in a0.RULES
