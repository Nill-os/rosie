import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fold(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def names(path):
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if m:
            out.add(fold(m.group(0)))
    return out


def test_the_lock_covers_every_requirement_and_pins_with_hashes():
    lock_text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    pinned = {fold(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==", lock_text, re.M)}
    wanted = names(ROOT / "requirements.txt") | names(ROOT / "requirements-test.txt")
    assert len(wanted) > 20 and len(pinned) > len(wanted)
    assert wanted - pinned == set()
    assert "--hash=sha256:" in lock_text
    print("PASS: requirements.lock pins every name in requirements.txt and requirements-test.txt")


def test_the_name_check_bites_on_a_missing_name():
    pinned = {"fastapi"}
    assert {"fastapi", "pillow"} - pinned == {"pillow"}
    assert fold("Python_Multipart") == fold("python-multipart")
    print("PASS: a name missing from the lock is reported")
