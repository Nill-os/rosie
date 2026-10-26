# board #917 safety-sandboxed-empty-data
import importlib.util
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("launcher", HERE / "launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def main():
    import tempfile
    root = Path(tempfile.mkdtemp(prefix="rosie-ci-"))
    launcher.ROOT, launcher.SLOTS = root, root / "slots"
    slot = launcher.SLOTS / "A"
    slot.mkdir(parents=True)
    for d in ("app", "static"):
        shutil.copytree(HERE.parent.parent / d, slot / d)
    launcher.venv_python = lambda s: Path(sys.executable)  # board #917 quirk-job-python-no-venv
    launcher.link_shared("A")
    res = launcher.smoke("A")
    print(res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
