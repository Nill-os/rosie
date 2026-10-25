"""One set of skills for Claude Code, Rosie and Agent Zero (skill_sync.py; owner 2026-10-20: "i want them all to have the same skills")."""
import json
import subprocess

import pytest

import skill_sync as ss
import skills


def _skill(root, name, desc="Use when testing.", body="1. Do the thing.", extra=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")
    for rel, text in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


@pytest.fixture
def world(monkeypatch):
    """Agent Zero's container stands in as a plain folder: docker exec's tar lands in ss.A0_SKILLS."""
    ss.CLAUDE_SKILLS.mkdir(parents=True)
    ss.A0_SKILLS.mkdir(parents=True)
    (ss.CLAUDE / "settings.json").write_text(json.dumps({"enabledPlugins": {"cad@x": True, "off@x": False}}))
    (ss.CLAUDE / "plugins").mkdir()
    calls = []

    def docker(*args, data=None, timeout=60):
        calls.append(args)
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, b"true\n", b"")
        cmd = args[-1]
        if args[:2] == ("exec", "-i"):
            target = cmd.split("'")[1].replace(ss.A0_PATH, str(ss.A0_SKILLS))
            subprocess.run(["sh", "-c", f"rm -rf '{target}' && mkdir -p '{target}' && tar -C '{target}' -xf -"], input=data, check=True)
        elif args[-3:-1] == ("rm", "-rf"):
            subprocess.run(["rm", "-rf", cmd.replace(ss.A0_PATH, str(ss.A0_SKILLS))], check=True)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(ss, "_docker", docker)
    return calls


def test_a_claude_skill_reaches_rosie_and_agent_zero(world):
    _skill(ss.CLAUDE_SKILLS, "prusa-xl", "Use for the owner's Prusa XL printer.", "Slice with PrusaSlicer.", {"scripts/slice.sh": "echo hi"})
    out = ss.sync()
    assert "rosie:prusa-xl" in out["added"] and "agent-zero:prusa-xl" in out["added"]
    pb = skills.use_skill("prusa-xl")["instructions"]
    assert pb.startswith("Use for the owner's Prusa XL printer.") and "Mirrored-from: claude:" in pb and "Skill folder:" not in pb   # ~/.claude is blocked to her (skill audit pass 2)
    assert "Slice with PrusaSlicer." in pb
    assert (ss.A0_SKILLS / "prusa-xl" / "scripts" / "slice.sh").read_text(encoding="utf-8") == "echo hi"
    assert (ss.A0_SKILLS / "prusa-xl" / ss.MARK).exists()
    assert ss.sync()["added"] == [] and ss.sync()["updated"] == []                       # nothing changed: nothing rewritten


def test_rosies_and_agent_zeros_own_skills_reach_the_others(world):
    skills.init()
    (skills.DIR / "garden-care.md").write_text("Use for plants and the garden.\n\n1. recall(kind=garden) first.\n")
    _skill(ss.A0_SKILLS, "harness2", "Use to run harness2 experiments.")
    ss.sync()
    claude = (ss.CLAUDE_SKILLS / "garden-care" / "SKILL.md").read_text(encoding="utf-8")
    assert 'description: "Use for plants and the garden."' in claude and "Rosie's playbook" in claude
    assert (ss.A0_SKILLS / "garden-care" / "SKILL.md").exists()
    assert not (ss.CLAUDE_SKILLS / "harness2").exists()                     # Agent Zero's own skill waits for the owner's OK
    held = ss.approve("harness2")                                            # no version: refused, and told the current one
    assert "error" in held and ss.approve("harness2", held["version"])["approved"] == "harness2"
    ss.sync()
    assert (ss.CLAUDE_SKILLS / "harness2" / ss.MARK).read_text(encoding="utf-8").startswith("agent-zero:")
    assert skills.copied("harness2") and not skills.copied("garden-care")


def test_enabled_plugin_skills_count_and_disabled_ones_do_not(world):
    for key, path in (("cad@x", "cadplug"), ("off@x", "offplug")):
        _skill(ss.CLAUDE / path / "skills", f"{path}-skill")
    (ss.CLAUDE / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "cad@x": [{"installPath": str(ss.CLAUDE / "cadplug")}], "off@x": [{"installPath": str(ss.CLAUDE / "offplug")}]}}))
    ss.sync()
    assert (skills.DIR / "cadplug-skill.md").exists() and not (skills.DIR / "offplug-skill.md").exists()


def test_an_agents_own_skill_is_never_replaced_and_copies_never_loop(world):
    skills.init()
    _skill(ss.CLAUDE_SKILLS, "meal-planning", "Claude's meal skill.")                        # Rosie has her own of that name
    out = ss.sync()
    assert "rosie:meal-planning" not in out["added"] and "Mirrored-from" not in skills.use_skill("meal-planning")["instructions"]
    names = {p.name for p in ss.CLAUDE_SKILLS.iterdir()}
    ss.sync()
    assert {p.name for p in ss.CLAUDE_SKILLS.iterdir()} == names and ss.sync()["added"] == []


def test_a_removed_source_takes_its_copies_with_it(world):
    d = _skill(ss.CLAUDE_SKILLS, "old-skill")
    ss.sync()
    import shutil
    shutil.rmtree(d)
    out = ss.sync()
    assert {"rosie:old-skill", "agent-zero:old-skill"} <= set(out["removed"])
    assert not (skills.DIR / "old-skill.md").exists() and not (ss.A0_SKILLS / "old-skill").exists()


def test_agent_zero_down_means_its_copies_wait(world, monkeypatch):
    monkeypatch.setattr(ss, "a0_up", lambda: False)
    _skill(ss.CLAUDE_SKILLS, "later")
    out = ss.sync()
    assert "rosie:later" in out["added"] and "agent-zero:later" not in out["added"] and "not running" in out["a0"]
    monkeypatch.setattr(ss, "a0_up", lambda: True)
    assert "agent-zero:later" in ss.sync()["added"]


def test_rosies_prompt_lists_copied_skills_by_name_only(world):
    _skill(ss.CLAUDE_SKILLS, "dfam-check", "A very long description " * 20)
    ss.sync()
    line = skills.prompt_line()
    assert "dfam-check" in line and "A very long description" not in line and "meal-planning: " in line


def test_a_bundled_skill_points_rosie_at_its_folder_and_follows_a_slot_switch(world, monkeypatch, tmp_path):
    """Skills shipped with Rosie (ops/bundle_skills.py) reach all agents; the folder pointer follows the live slot."""
    for slot in ("A", "B"):
        _skill(tmp_path / slot / "skills", "gcode", "Use to slice models.", "Slice it.", {"references/x.md": "ref"})
    monkeypatch.setattr(ss, "BUNDLE", tmp_path / "A" / "skills")
    assert "rosie:gcode" in ss.sync()["added"]
    assert f"Skill folder: {tmp_path / 'A' / 'skills' / 'gcode'}" in skills.use_skill("gcode")["instructions"]
    assert (ss.A0_SKILLS / "gcode" / "references" / "x.md").read_text(encoding="utf-8") == "ref"
    monkeypatch.setattr(ss, "BUNDLE", tmp_path / "B" / "skills")                         # an update switched slots
    assert "rosie:gcode" in ss.sync()["updated"]
    assert f"Skill folder: {tmp_path / 'B' / 'skills' / 'gcode'}" in skills.use_skill("gcode")["instructions"]


def test_agent_zero_place_comes_from_a0_host(monkeypatch, tmp_path):
    """No stand-in folder or container: the folder and name are read from a0_host at call time (Settings or detection)."""
    import a0_host
    monkeypatch.setattr(ss, "A0_SKILLS", None)
    monkeypatch.setattr(ss, "CONTAINER", None)
    monkeypatch.setattr(a0_host, "info", lambda: {"container": "az-two", "url": "http://127.0.0.1:1", "usr": str(tmp_path / "usr")})
    assert ss._a0_dir() == tmp_path / "usr" / "skills" and ss._container() == "az-two"


def test_a_missing_claude_skills_folder_is_created_for_the_copy(world, tmp_path):
    import shutil
    shutil.rmtree(ss.CLAUDE_SKILLS)
    assert ss._write_claude("x-skill", {"origin": "rosie:x"}, {"SKILL.md": b"---\nname: x-skill\n---\n"})
    assert (ss.CLAUDE_SKILLS / "x-skill" / "SKILL.md").exists() and (ss.CLAUDE_SKILLS / "x-skill" / ss.MARK).exists()


def test_the_old_claude_copy_survives_a_failed_swap(world, monkeypatch):
    from pathlib import Path
    ss._write_claude("x-skill", {"origin": "rosie:x"}, {"SKILL.md": b"old"})
    real = Path.rename

    def rename(self, target):
        if self.name.startswith(".x-skill.") and not self.name.startswith(".x-skill.old."):
            raise OSError("swap failed")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError):
        ss._write_claude("x-skill", {"origin": "rosie:x"}, {"SKILL.md": b"new"})
    assert (ss.CLAUDE_SKILLS / "x-skill" / "SKILL.md").read_bytes() == b"old"
    assert [p.name for p in ss.CLAUDE_SKILLS.iterdir() if p.name.startswith(".")] == []


def test_a_playbook_that_quotes_the_marker_in_its_body_stays_an_original(world):
    (skills.DIR).mkdir(parents=True, exist_ok=True)
    (skills.DIR / "notes.md").write_text("Use for notes.\n\n" + "1. step\n" * 10 + "\nMirrored-from: claude:/x\n", encoding="utf-8")
    assert not skills.copied("notes") and not ss._marked((skills.DIR / "notes.md").read_text(encoding="utf-8"))
    assert ss._marked("Use it.\n\nMirrored-from: claude:/x\nbody")


def test_a_symlink_loop_inside_a_skill_is_skipped_not_fatal(tmp_path):
    """Audit 2026-10-25: Path.resolve() raises RuntimeError on a loop in Python 3.12, which stopped every skill's sync."""
    d = tmp_path / "sk"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: sk\n---\nx\n")
    try:
        (d / "loop").symlink_to("loop")
    except OSError:
        pytest.skip("no symlinks here")
    assert set(ss._files({"dir": d, "text": "x"})) == {"SKILL.md"}
