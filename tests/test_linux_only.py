import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
APP = TESTS.parent / "app"
POSIX = re.compile(r"^(?:import|from) (?:fcntl|termios|pwd|grp|resource|pty)\b", re.M)
IMPORT = re.compile(r"^(?:import|from) (\w+)", re.M)
SHELL = re.compile(r'\[\s*"(ba)?sh"|"/bin/(ba)?sh"')


def _linux_only():
    s = (TESTS / "conftest.py").read_text(encoding="utf-8")
    block = re.search(r"^LINUX_ONLY = \[(.*?)\]", s, re.S | re.M).group(1)
    return set(re.findall(r'"([^"]+\.py)"', block))


def test_every_test_that_runs_a_posix_shell_is_skipped_on_windows():
    shell = {f.name for f in TESTS.glob("test_*.py") if SHELL.search(f.read_text(encoding="utf-8"))}
    assert shell - _linux_only() == set()


def test_every_test_that_imports_a_posix_only_module_is_skipped_on_windows():
    posix = {f.stem for f in APP.glob("*.py") if POSIX.search(f.read_text(encoding="utf-8"))}
    hit = {f.name for f in TESTS.glob("test_*.py") if posix & set(IMPORT.findall(f.read_text(encoding="utf-8")))}
    assert hit - _linux_only() == set()
