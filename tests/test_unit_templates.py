import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNITS = sorted((ROOT / "ops" / "units").glob("*.service"))
RESOLVE = 'R=%h/assistant; [ "$$(cat %h/rosie-ops/active 2>/dev/null)" = B ] && R=%h/assistant-b; [ -f "$$R/ops/units/%n" ] || { [ "$$R" = %h/assistant ] && R=%h/assistant-b || R=%h/assistant; }'


def test_the_notes_and_phone_units_are_shipped():
    assert "assistant-notes-mcp.service" in {u.name for u in UNITS}


def test_every_unit_template_resolves_the_active_slot():
    for u in UNITS:
        text = u.read_text()
        assert RESOLVE in text, u.name
        assert "WorkingDirectory" not in text, u.name
        assert not re.search(r"/home/|rosie-dev-", text), u.name
        for line in re.findall(r"^Exec\w*=.*$", text, re.M):
            assert "%h/assistant" not in line.replace(RESOLVE, ""), (u.name, line)


def test_the_installer_copies_the_unit_templates():
    assert "units/*.service" in (ROOT / "ops" / "install.sh").read_text()


def test_install_script_heredoc_units_use_no_fixed_slot_path():
    text = (ROOT / "ops" / "install.sh").read_text()
    for line in re.findall(r"^Exec\w*=.*$", text, re.M):
        assert "%h/assistant" not in line, line


def _chosen(unit, home, active):
    cmd = re.search(r"^ExecStart=/bin/sh -c '(.*)'$", unit.read_text(), re.M).group(1)
    cmd = cmd.split("; cd ")[0].replace("%h", str(home)).replace("%n", unit.name).replace("$$", "$")
    if active:
        (home / "rosie-ops").mkdir(exist_ok=True)
        (home / "rosie-ops" / "active").write_text(active)
    return subprocess.run(["sh", "-c", cmd + '; echo "$R"'], capture_output=True, text=True, check=True).stdout.strip()


def test_a_unit_never_starts_from_a_slot_that_does_not_ship_it(tmp_path):
    for u in UNITS:
        home = tmp_path / u.stem
        a, b = home / "assistant", home / "assistant-b"
        for slot in (a, b):
            (slot / "ops" / "units").mkdir(parents=True)
        (a / "ops" / "units" / u.name).write_text("x")
        assert _chosen(u, home, "") == str(a), u.name
        assert _chosen(u, home, "B") == str(a), u.name
        (b / "ops" / "units" / u.name).write_text("x")
        assert _chosen(u, home, "B") == str(b), u.name
        (a / "ops" / "units" / u.name).unlink()
        assert _chosen(u, home, "A") == str(b), u.name


def test_a_uvicorn_unit_never_writes_request_paths_to_the_journal():
    for u in UNITS:
        s = u.read_text()
        if "uvicorn" in s:
            assert "--no-access-log" in s, u.name


def _unit_copy_block():
    text = (ROOT / "ops" / "install.sh").read_text()
    m = re.search(r"^if compgen -G .*?^fi$", text, re.M | re.S)
    assert m, "install.sh has no guarded unit copy"
    return m.group(0)


def _run_copy(tmp_path, src):
    dest = tmp_path / ".config" / "systemd" / "user"
    dest.mkdir(parents=True)
    r = subprocess.run(["bash", "-e", "-c", _unit_copy_block()], env={"SRC": str(src), "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    return r, sorted(p.name for p in dest.iterdir())


def test_the_installer_skips_a_missing_or_empty_units_folder(tmp_path):
    (tmp_path / "empty" / "units").mkdir(parents=True)
    for src in (tmp_path / "none", tmp_path / "empty"):
        r, copied = _run_copy(tmp_path / src.name, src)
        assert r.returncode == 0 and copied == [] and "skipping the unit copy" in r.stdout, (src, r.stderr)


def test_the_installer_copies_units_when_the_folder_has_them(tmp_path):
    (tmp_path / "s" / "units").mkdir(parents=True)
    (tmp_path / "s" / "units" / "x.service").write_text("[Unit]\n")
    r, copied = _run_copy(tmp_path / "h", tmp_path / "s")
    assert r.returncode == 0 and copied == ["x.service"]
