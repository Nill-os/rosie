# board #884 quirk-windows-unix-calls-review
import json
import os
import subprocess
import sys

import pytest

import agentboard as board
import skill_sync as ss
import skills
from conftest import needs_symlinks
from test_skill_sync import _skill, world  # noqa: F401  (the fixture: Agent Zero's container as a plain folder)


def _own_text(who):
    return f"---\nname: dup\ndescription: {who}'s own\n---\n\nthis is {who}'s own text\n"


def _snapshot(root, only="dup"):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted((root / only).rglob("*")) if p.is_file()}


def test_C1_an_own_skill_of_the_same_name_is_never_overwritten_by_sync(world):
    skills.init()
    (ss.CLAUDE_SKILLS / "dup").mkdir()
    (ss.CLAUDE_SKILLS / "dup" / "SKILL.md").write_text(_own_text("claude"))
    (skills.DIR / "dup.md").write_text("Rosie's own.\n\nrosie body\n")
    (ss.A0_SKILLS / "dup").mkdir()
    (ss.A0_SKILLS / "dup" / "SKILL.md").write_text(_own_text("a0"))
    before = (_snapshot(ss.CLAUDE_SKILLS), _snapshot(ss.A0_SKILLS), (skills.DIR / "dup.md").read_bytes())
    for _ in range(2):
        out = ss.sync()
    assert (_snapshot(ss.CLAUDE_SKILLS), _snapshot(ss.A0_SKILLS), (skills.DIR / "dup.md").read_bytes()) == before
    assert not any(k.endswith(":dup") for k in out["added"] + out["updated"] + out["removed"])


@pytest.mark.parametrize("target", ["claude", "rosie", "agent-zero"])
def test_C1_the_writers_refuse_an_own_skill(world, target):
    skills.init()
    src = {"origin": "claude:x", "dir": None, "text": "---\nname: dup\ndescription: d\n---\n\nbody\n"}
    if target == "claude":
        (ss.CLAUDE_SKILLS / "dup").mkdir()
        (ss.CLAUDE_SKILLS / "dup" / "SKILL.md").write_text("mine")
        assert ss._write_claude("dup", src, {"SKILL.md": b"theirs"}) is False
        assert _snapshot(ss.CLAUDE_SKILLS) == {"dup/SKILL.md": b"mine"}
    elif target == "rosie":
        (skills.DIR / "dup.md").write_text("mine\n")
        assert ss._write_rosie("dup", src) is False and (skills.DIR / "dup.md").read_text() == "mine\n"
    else:
        (ss.A0_SKILLS / "dup").mkdir()
        (ss.A0_SKILLS / "dup" / "SKILL.md").write_text("mine")
        assert ss._write_a0("dup", src, {"SKILL.md": b"theirs"}) is False
        assert _snapshot(ss.A0_SKILLS) == {"dup/SKILL.md": b"mine"}


def test_C1_a_stale_state_entry_never_deletes_an_own_skill(world):
    # board #884 safety-owner-folder-stays
    skills.init()
    (ss.CLAUDE_SKILLS / "dup").mkdir()
    (ss.CLAUDE_SKILLS / "dup" / "SKILL.md").write_text(_own_text("claude"))
    (skills.DIR / "dup.md").write_text("Rosie's own.\n\nbody\n")
    (ss.A0_SKILLS / "dup").mkdir()
    (ss.A0_SKILLS / "dup" / "SKILL.md").write_text(_own_text("a0"))
    ss.STATE.parent.mkdir(parents=True, exist_ok=True)
    ss.STATE.write_text(json.dumps({"claude:gone": "x", "rosie:gone": "x", "agent-zero:gone": "x"}))
    for agent, p in (("claude", ss.CLAUDE_SKILLS / "gone"), ("agent-zero", ss.A0_SKILLS / "gone")):
        p.mkdir()
        (p / "SKILL.md").write_text("own " + agent)
    (skills.DIR / "gone.md").write_text("Own playbook.\n\nbody\n")
    out = ss.sync()
    assert (ss.CLAUDE_SKILLS / "gone" / "SKILL.md").exists() and (ss.A0_SKILLS / "gone" / "SKILL.md").exists() and (skills.DIR / "gone.md").exists()
    assert (ss.CLAUDE_SKILLS / "dup" / "SKILL.md").exists() and (skills.DIR / "dup.md").exists() and (ss.A0_SKILLS / "dup" / "SKILL.md").exists()
    assert "claude:gone" in out["removed"]


def test_C1_a_playbook_that_quotes_the_mark_after_line_8_stays_an_original(world):
    # board #884 safety-quoted-mark-original
    skills.init()
    body = "\n".join(f"step {i}" for i in range(12)) + "\nMirrored-from: claude:/x\nmore\n"
    (skills.DIR / "quoter.md").write_text("Use when quoting.\n\n" + body)
    assert not ss._marked((skills.DIR / "quoter.md").read_text())
    assert "quoter" in ss.rosie_skills()
    out = ss.sync()
    assert "claude:quoter" in out["added"] and "agent-zero:quoter" in out["added"]
    src = {"origin": "claude:x", "dir": None, "text": "---\nname: quoter\ndescription: d\n---\n\nnew\n"}
    before = (skills.DIR / "quoter.md").read_bytes()
    assert ss._write_rosie("quoter", src) is False and (skills.DIR / "quoter.md").read_bytes() == before
    ss._remove("rosie", "quoter")
    assert (skills.DIR / "quoter.md").read_bytes() == before


def test_C1_a_real_mark_in_the_header_is_a_copy():
    # board #884 why-mark-on-first-lines
    assert ss._marked("desc\n\nMirrored-from: claude:/x\nbody\n")
    assert not ss._marked("\n".join(["l"] * 8) + "\nMirrored-from: x\n")


def _break(kind, d):
    md = d / "SKILL.md"
    if kind == "unreadable file":
        (d / "secret.txt").write_text("x")
        (d / "secret.txt").chmod(0)
    elif kind == "unreadable SKILL.md":
        md.chmod(0)
    elif kind == "symlink loop":
        os.symlink(d / "loop", d / "loop")
    elif kind == "dir symlink loop":
        (d / "a").mkdir()
        os.symlink(d / "a", d / "a" / "b")
    elif kind == "bad frontmatter":
        md.write_text("---\nname: [oops\ndescription: \"unterminated\n")
    elif kind == "no frontmatter":
        md.write_text("just text, no header")
    elif kind == "huge file":
        with open(d / "big.bin", "wb") as f:
            f.truncate(ss.MAX_FILE + 10)
    elif kind == "non-utf8 SKILL.md":
        md.write_bytes(b"---\nname: bad\ndescription: caf\xe9 \xff\xfe\n---\n\nbody \x80\x81\n")
    elif kind == "non-utf8 file":
        (d / "notes.txt").write_bytes(b"\xff\xfe\x00bad")
    elif kind == "empty SKILL.md":
        md.write_text("")
    elif kind == "dangling symlink":
        os.symlink(d / "nowhere", d / "dangling")
    elif kind == "long name":
        pass


KINDS = ["unreadable file", "unreadable SKILL.md", "symlink loop", "dir symlink loop", "bad frontmatter", "no frontmatter", "huge file",
         "non-utf8 SKILL.md", "non-utf8 file", "empty SKILL.md", "dangling symlink"]


@pytest.mark.parametrize("kind", KINDS)
def test_C2_one_broken_skill_does_not_stop_the_others(world, kind):
    _skill(ss.CLAUDE_SKILLS, "aaa-first", body="first")
    bad = _skill(ss.CLAUDE_SKILLS, "bad", body="bad one")
    _skill(ss.CLAUDE_SKILLS, "zzz-good", body="good one", extra={"scripts/run.sh": "echo ok"})
    try:
        _break(kind, bad)
    except OSError:
        pytest.skip("this system can't build the defect")
    try:
        out = ss.sync()
    finally:
        for p in bad.rglob("*"):
            try:
                p.chmod(0o644)
            except OSError:
                pass
        try:
            (bad / "SKILL.md").chmod(0o644)
        except OSError:
            pass
    assert "errors" not in out or all("good" not in e and "first" not in e for e in out["errors"])
    for n in ("aaa-first", "zzz-good"):
        assert f"rosie:{n}" in out["added"] and f"agent-zero:{n}" in out["added"], (n, out)
    assert (ss.A0_SKILLS / "zzz-good" / "scripts" / "run.sh").read_text() == "echo ok"
    assert skills.use_skill("zzz-good")["instructions"]
    if kind == "huge file":
        assert not (ss.A0_SKILLS / "bad" / "big.bin").exists()


def test_C2_a_broken_skill_in_each_agents_own_folder_does_not_stop_the_round(world):
    skills.init()
    (skills.DIR / "broken-pb.md").write_bytes(b"Use when \xff\xfe.\n\nbody \x80\n")
    _skill(ss.A0_SKILLS, "a0-bad").joinpath("SKILL.md").write_text("---\n: : :\n---\n")
    _skill(ss.CLAUDE_SKILLS, "fine", body="fine body")
    out = ss.sync()
    assert "rosie:fine" in out["added"] and "agent-zero:fine" in out["added"]


def test_C2_a_failing_write_to_one_target_is_reported_and_the_rest_continue(world, monkeypatch):
    _skill(ss.CLAUDE_SKILLS, "aaa-bad", body="x")
    _skill(ss.CLAUDE_SKILLS, "bbb-ok", body="y")
    real = ss._write_a0

    def flaky(name, src, files):
        if name == "aaa-bad":
            raise OSError("docker exec failed: boom")
        return real(name, src, files)
    monkeypatch.setattr(ss, "_write_a0", flaky)
    out = ss.sync()
    assert any("aaa-bad" in e for e in out["errors"])
    assert "agent-zero:bbb-ok" in out["added"] and "rosie:bbb-ok" in out["added"] and "rosie:aaa-bad" in out["added"]
    assert (ss.A0_SKILLS / "bbb-ok" / "SKILL.md").exists()


def test_C2_a_failing_write_for_claude_leaves_no_half_copy_and_the_rest_continue(world, monkeypatch):
    skills.init()
    (skills.DIR / "aaa-pb.md").write_text("Use for a.\n\nbody\n")
    (skills.DIR / "bbb-pb.md").write_text("Use for b.\n\nbody\n")
    real = ss._write_claude

    def flaky(name, src, files):
        if name == "aaa-pb":
            raise OSError("disk full")
        return real(name, src, files)
    monkeypatch.setattr(ss, "_write_claude", flaky)
    out = ss.sync()
    assert any("claude:aaa-pb" in e for e in out["errors"]) and "claude:bbb-pb" in out["added"]
    assert [p.name for p in ss.CLAUDE_SKILLS.iterdir() if p.name.startswith(".")] == []


def _outside(tmp_path):
    out = tmp_path / "outside"
    (out / "sub").mkdir(parents=True)
    (out / "secret.txt").write_text("OUTSIDE-SECRET")
    (out / "sub" / "deep.txt").write_text("OUTSIDE-SECRET-DEEP")
    return out


def _leaks(*roots):
    return [str(p) for r in roots for p in r.rglob("*") if p.is_file() and not p.is_symlink() and b"OUTSIDE-SECRET" in p.read_bytes()]


@needs_symlinks
def test_C3_a_symlink_to_outside_is_never_copied(world, tmp_path):
    out = _outside(tmp_path)
    d = _skill(ss.CLAUDE_SKILLS, "linky", extra={"real.txt": "fine", "refs/ok.md": "ok"})
    os.symlink(out / "secret.txt", d / "file-link.txt")
    os.symlink(out, d / "dir-link")
    os.symlink(out / "sub", d / "refs" / "nested-link")
    os.symlink("../../outside/secret.txt", d / "rel-link.txt")
    os.symlink("..", d / "up")
    files = ss._files({"dir": d, "text": "", "origin": "claude:x"})
    assert sorted(files) == ["SKILL.md", "real.txt", "refs/ok.md"]
    ss.sync()
    assert (ss.A0_SKILLS / "linky" / "real.txt").exists()
    assert _leaks(ss.A0_SKILLS / "linky") == [] and not any(p.is_symlink() for p in (ss.A0_SKILLS / "linky").rglob("*"))
    assert sorted(p.name for p in (ss.A0_SKILLS / "linky").rglob("*") if p.is_file()) == [ss.MARK, "SKILL.md", "ok.md", "real.txt"]
    assert "OUTSIDE" not in skills.use_skill("linky")["instructions"]


@needs_symlinks
def test_C3_a_symlinked_skill_md_or_skill_folder_is_not_a_skill(world, tmp_path):
    out = _outside(tmp_path)
    (out / "SKILL.md").write_text("---\nname: stolen\ndescription: OUTSIDE-SECRET\n---\n\nOUTSIDE-SECRET\n")
    (ss.CLAUDE_SKILLS / "md-link").mkdir()
    os.symlink(out / "SKILL.md", ss.CLAUDE_SKILLS / "md-link" / "SKILL.md")
    os.symlink(out, ss.CLAUDE_SKILLS / "dir-link-skill")
    _skill(ss.CLAUDE_SKILLS, "fine")
    ss.sync()
    assert (ss.A0_SKILLS / "fine" / "SKILL.md").exists()
    assert not (ss.A0_SKILLS / "md-link").exists() and not (ss.A0_SKILLS / "stolen").exists() and not (ss.A0_SKILLS / "dir-link-skill").exists()
    assert _leaks(ss.A0_SKILLS, ss.CLAUDE_SKILLS / "fine") == [] and not (skills.DIR / "stolen.md").exists()


@needs_symlinks
def test_C3_a_link_inside_agent_zeros_own_skill_is_not_followed(world, tmp_path):
    out = _outside(tmp_path)
    d = _skill(ss.A0_SKILLS, "a0-skill")
    os.symlink(out / "secret.txt", d / "notes.txt")
    os.symlink(out, d / "dir")
    ss.approve("a0-skill", ss.approve("a0-skill")["version"])
    ss.sync()
    assert (ss.CLAUDE_SKILLS / "a0-skill" / "SKILL.md").exists()
    assert _leaks(ss.CLAUDE_SKILLS) == [] and "OUTSIDE" not in skills.use_skill("a0-skill")["instructions"]
    assert sorted(p.name for p in (ss.CLAUDE_SKILLS / "a0-skill").rglob("*") if p.is_file()) == [ss.MARK, "SKILL.md"]


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions exist on Windows only")
def test_C3_a_junction_to_outside_is_never_copied(world, tmp_path):
    out = _outside(tmp_path)
    d = _skill(ss.CLAUDE_SKILLS, "junky")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(d / "jn"), str(out)], capture_output=True)
    assert r.returncode == 0
    ss.sync()
    assert _leaks(ss.A0_SKILLS / "junky", ss.CLAUDE_SKILLS / "junky") == []


NOTICES = {
    "task-notification": "<task-notification>\n<task-id>b1</task-id> completed</task-notification>",
    "task-notification with leading space": "  \n<task-notification>x</task-notification>",
    "SYSTEM NOTIFICATION": "[SYSTEM NOTIFICATION - NOT USER INPUT] a background task finished",
    "Stop hook feedback": "Stop hook feedback:\n[Agent board] you posted nothing",
    "cross-session message": "<cross-session-message from=\"x\">hi</cross-session-message>",
    "Cross-session bracket": "[Cross-session message from claude-9] hi",
    "Another Claude session": "Another Claude session sent a message:\n<agent-message from=\"a1\">report</agent-message>",
    "system-reminder": "<system-reminder>a background job ended</system-reminder>",
    "lower case": "another claude session sent a message: report",
}


def _session(monkeypatch, prompt="fix the build"):
    import board_hook
    monkeypatch.setenv("ROSIE_AGENT", "claude-1")
    board_hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": prompt})
    return board_hook


@pytest.mark.parametrize("form", sorted(NOTICES))
def test_C5_a_notice_after_a_post_keeps_the_stop_hook_quiet(monkeypatch, form):
    hook = _session(monkeypatch)
    board.post("fixing the build", "doing", agent="claude-1")
    for _ in range(3):
        hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": NOTICES[form]})
        for _ in range(board.WORK_CALLS + 2):
            out = hook.main("PostToolUse", {"tool_name": "Edit", "tool_input": {}})
            assert out is None or "doing" not in out["hookSpecificOutput"]["additionalContext"], form
        assert hook.main("Stop", {"stop_hook_active": False}) is None, form


def test_C5_the_notice_never_becomes_the_task(monkeypatch):
    hook = _session(monkeypatch, "deploy the Canvas sync")
    for n in NOTICES.values():
        hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": n})
    with board._db() as c:
        task = c.execute("SELECT task FROM sessions WHERE agent='claude-1'").fetchone()[0]
    assert task == "deploy the Canvas sync"


def test_C5_a_real_prompt_after_a_post_starts_a_new_count(monkeypatch):
    hook = _session(monkeypatch)
    board.post("fixing the build", "doing", agent="claude-1")
    hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": NOTICES["task-notification"]})
    assert hook.main("Stop", {}) is None
    hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": "now rewrite the docs"})
    nudges = [hook.main("PostToolUse", {"tool_name": "Edit", "tool_input": {}}) for _ in range(board.WORK_CALLS + 2)]
    assert any(n and "doing" in n["hookSpecificOutput"]["additionalContext"] for n in nudges)
    stop = hook.main("Stop", {"stop_hook_active": False})
    assert stop and stop["decision"] == "block" and "posted nothing" in stop["reason"]


@pytest.mark.parametrize("prompt", ["please fix the <task-notification> parser",
                                    "Another Claude session would help here", "the system notification in the app is wrong"])
def test_C5_a_real_prompt_that_only_mentions_a_notice_word_is_still_a_new_task(monkeypatch, prompt):
    hook = _session(monkeypatch)
    board.post("fixing the build", "doing", agent="claude-1")
    hook.main("UserPromptSubmit", {"cwd": "/tmp/x", "prompt": prompt})
    for _ in range(board.WORK_CALLS + 2):
        hook.main("PostToolUse", {"tool_name": "Edit", "tool_input": {}})
    stop = hook.main("Stop", {"stop_hook_active": False})
    assert stop and "posted nothing" in stop["reason"]


def test_C2_an_unreadable_rosie_playbook_does_not_stop_the_round(world):
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("chmod 0 doesn't block reads here")
    skills.init()
    bad = skills.DIR / "locked.md"
    bad.write_text("Use when locked.\n\nbody\n")
    bad.chmod(0)
    try:
        _skill(ss.CLAUDE_SKILLS, "fine2", body="fine body")
        out = ss.sync()
        assert "rosie:fine2" in out["added"]
        assert "locked" not in dict(skills.listing())
    finally:
        bad.chmod(0o644)
