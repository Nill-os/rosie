import os
import re
from pathlib import Path

import policy

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("ASSISTANT_MCP_HOME") or Path.home()).resolve()
MAX_BYTES = 200_000

# board #729 safety-credential-paths-blocked
DENY_PARTS = policy.DENY_PARTS
DENY_PREFIXES = policy.DENY_PREFIXES
DENY_NAME = policy.DENY_NAME
SECRET_PATTERNS = [(re.compile(r"://[^/\s:@]+:[^@\s/]{3,}@"), "://[REDACTED url credentials]@"), (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED google key]"), (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[REDACTED api key]"),
                   (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED github token]"), (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED slack token]"),
                   (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED aws key]"), (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), "[REDACTED google oauth]"),
                   (re.compile(r"(?i)(?<![A-Za-z0-9])[\w.-]{0,40}(password|passwd|secret|token|api[_-]?key)\w{0,20}\s*[:=]\s*['\"]?[^\s'\"]{8,}"), "[REDACTED credential]"),
                   (re.compile(r'(?i)"[^"\n]{0,40}(?:password|passwd|secret|token|api[_-]?key|authorization)[^"\n]{0,40}"\s*:\s*"[^"\n]{8,}"'), '"[REDACTED credential]": "[REDACTED]"'),
                   (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"), "Bearer [REDACTED]"),
                   (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), "[REDACTED private key]"),
                   (re.compile(r"(?i)\bbasic[ \t]+(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/=]{12,}"), "Basic [REDACTED]"),
                   (re.compile(r"assistant_session=\S+"), "assistant_session=[REDACTED]"),
                   (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[REDACTED jwt]"),
                   (re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,90}\b"), "[REDACTED possible private key]"),
                   (re.compile(r"\b(?:[a-z]{3,8} ){11}[a-z]{3,8}\b(?=.{0,300}\bseed\b)", re.I), "[REDACTED possible seed phrase]")]
PRIVATE_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


class Blocked(Exception):
    pass


def resolve(path: str) -> Path:
    raw = (path or "~").strip()
    if raw == "~" or raw.startswith("~/"):
        p = HOME / raw[2:]
    else:
        p = Path(raw)
        if not p.is_absolute():
            p = HOME / p
    try:
        real = p.resolve()  # board #729 safety-symlinks-resolved
    except (OSError, RuntimeError):
        raise Blocked("That path could not be resolved.")
    if real != HOME and HOME not in real.parents:
        raise Blocked("Only files inside the home folder can be read.")
    rel = real.relative_to(HOME)
    parts = [x.lower() for x in rel.parts]
    shipped = ROOT.resolve() / "skills"
    if (real == shipped or shipped in real.parents) and not (real.name and DENY_NAME.match(real.name)):
        return real
    if any(x in {d.lower() for d in DENY_PARTS} for x in parts):
        raise Blocked("That location holds credentials or private app data and is blocked.")
    rels = rel.as_posix().lower()  # board #729 quirk-windows-backslash-paths
    if any(rels == pre.lower() or rels.startswith(pre.lower() + "/") for pre in DENY_PREFIXES):
        raise Blocked("That location holds credentials or private app data and is blocked.")
    if real.name and DENY_NAME.match(real.name):
        raise Blocked("That file looks like a key, token, password or credential store and is blocked.")
    for private in (ROOT / "secrets", ROOT / "data"):
        pr = private.resolve()
        if real == pr or pr in real.parents:
            raise Blocked("The assistant's own secrets and databases are blocked.")
    return real


def redact(text: str):
    n = 0
    for rx, repl in SECRET_PATTERNS:
        text, k = rx.subn(repl, text)
        n += k
    return text, n


def read_text(path: str, max_chars: int = 20000) -> dict:
    p = resolve(path)
    if not p.is_file():
        raise Blocked("Not a file.")
    size = p.stat().st_size
    with p.open("rb") as f:
        head = f.read(4096)
        if b"\0" in head:
            return {"path": str(p), "binary": True, "size": size, "note": "Binary file: contents not returned."}
        f.seek(0)
        raw = f.read(MAX_BYTES)
    text = raw.decode("utf-8", errors="replace")
    if PRIVATE_BLOCK.search(text):
        raise Blocked("That file contains a private key and is blocked.")
    text, n = redact(text)
    cut = len(text) > max_chars
    return {"path": str(p), "size": size, "truncated": cut or size > MAX_BYTES, "redactions": n, "text": text[:max_chars]}


def list_dir(path: str, limit: int = 200) -> dict:
    p = resolve(path)
    if not p.is_dir():
        raise Blocked("Not a folder.")
    out = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            resolve(str(e))
        except Blocked:
            continue
        out.append(e.name + ("/" if e.is_dir() else ""))
        if len(out) >= limit:
            break
    return {"path": str(p), "entries": out}
