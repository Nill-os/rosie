import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
APP = TESTS.parent / "app"
POSIX_MODULES = {"fcntl", "termios", "pwd", "grp", "resource", "pty"}
SHELL = re.compile(r'\[\s*"(ba)?sh"|"/bin/(ba)?sh"')


PLATFORM_TOKENS = ("os.name", "sys.platform", "platform.system", "TYPE_CHECKING")
CATCHES = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _calls(func, names):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in names for n in ast.walk(func))


def _catches(handler):
    t = handler.type
    if t is None:
        return True
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    return any((isinstance(e, ast.Name) and e.id in CATCHES) or (isinstance(e, ast.Attribute) and e.attr in CATCHES) for e in elts)


def _guards(node, source):
    if isinstance(node, ast.If):
        return any(tok in (ast.get_source_segment(source, node.test) or "") for tok in PLATFORM_TOKENS)
    if isinstance(node, ast.Try):
        return any(_catches(h) for h in node.handlers)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _calls(node, {"skip", "importorskip"})
    return False


def imported_modules(source):
    names = set()

    def visit(node, guarded):
        guarded = guarded or _guards(node, source)
        if not guarded:
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
                if node.module == "app":
                    names.update(a.name for a in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(ast.parse(source), False)
    return names


def offenders(app_dir, tests_dir, linux_only):
    posix = {f.stem for f in app_dir.glob("*.py") if imported_modules(f.read_text(encoding="utf-8")) & POSIX_MODULES}
    bad = set()
    for f in tests_dir.glob("test_*.py"):
        mods = imported_modules(f.read_text(encoding="utf-8"))
        if (mods & POSIX_MODULES) or (mods & posix):
            bad.add(f.name)
    return bad - linux_only


def parse_linux_only(source):
    for node in ast.parse(source).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "LINUX_ONLY" for t in targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                pytest.fail("tests/conftest.py: LINUX_ONLY must be a plain list of string literals")
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                pytest.fail("tests/conftest.py: LINUX_ONLY must be a plain list of string literals")
            return set(value)
    pytest.fail("tests/conftest.py: no top-level LINUX_ONLY assignment found")


def _linux_only():
    return parse_linux_only((TESTS / "conftest.py").read_text(encoding="utf-8"))


def test_the_import_walk_sees_multi_from_and_nested_imports():
    src = "import os, fcntl\nfrom termios import x\ndef f():\n    import pty\n    from . import local\n"
    assert imported_modules(src) == {"os", "fcntl", "termios", "pty"}
    guarded = "try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\nif os.name != 'nt':\n    import pty\ndef g():\n    pytest.skip('x')\n    import grp\n"
    assert imported_modules(guarded) == set()


def test_linux_only_parse_handles_reformatting_and_fails_clearly():
    assert parse_linux_only('LINUX_ONLY: list = [\n  "a.py",\n  "b.py",\n]\n') == {"a.py", "b.py"}
    with pytest.raises(pytest.fail.Exception, match="no top-level LINUX_ONLY"):
        parse_linux_only("x = 1\n")
    with pytest.raises(pytest.fail.Exception, match="plain list of string literals"):
        parse_linux_only("LINUX_ONLY = make()\n")


def test_every_test_that_runs_a_posix_shell_is_skipped_on_windows():
    shell = {f.name for f in TESTS.glob("test_*.py") if SHELL.search(f.read_text(encoding="utf-8"))}
    assert shell - _linux_only() == set()


def test_every_test_that_imports_a_posix_only_module_is_skipped_on_windows():
    assert offenders(APP, TESTS, _linux_only()) == set()


def test_the_guard_only_exempts_real_platform_guards():
    plain_if = "if DEBUG:\n    import fcntl\n"
    plain_try = "try:\n    import fcntl\nexcept KeyError:\n    pass\n"
    assert imported_modules(plain_if) == {"fcntl"}
    assert imported_modules(plain_try) == {"fcntl"}
    assert imported_modules("if os.name != 'nt':\n    import fcntl\n") == set()
    assert imported_modules("if sys.platform == 'linux':\n    import fcntl\n") == set()
    assert imported_modules("try:\n    import fcntl\nexcept (OSError, ImportError):\n    fcntl = None\n") == set()
    assert imported_modules("def f():\n    pytest.importorskip('x')\n    import fcntl\n") == set()


def test_a_planted_test_importing_a_posix_app_module_is_caught(tmp_path):
    app, tests = tmp_path / "app", tmp_path / "tests"
    app.mkdir()
    tests.mkdir()
    (app / "lock.py").write_text("import fcntl\n")
    (app / "safe.py").write_text("try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\n")
    (tests / "test_a.py").write_text("import lock\n")
    (tests / "test_b.py").write_text("from app import lock\n")
    (tests / "test_c.py").write_text("import safe\n")
    (tests / "test_d.py").write_text("if DEBUG:\n    import termios\n")
    assert offenders(app, tests, set()) == {"test_a.py", "test_b.py", "test_d.py"}
    assert offenders(app, tests, {"test_a.py"}) == {"test_b.py", "test_d.py"}
