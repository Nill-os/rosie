"""Write a file so a reader (or a crash) only ever sees the old content or the new one, never half (audit 2026-10-20: settings and
keys were rewritten in place or through one shared temp file). The temp file is private (0600) from its first byte."""
import os
import tempfile
from pathlib import Path

import testguard


def write_text(path, text: str, mode: int | None = None):
    path = Path(path)
    testguard.check(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
