"""What the outside world (MCP / cloud AI agents) may READ. Read-only. Credential stores are blocked and secrets are redacted.
This is a safety net on top of your choice to share all folders: it exists because cloud agents send what they read to a cloud model."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("ASSISTANT_MCP_HOME") or Path.home()).resolve()
MAX_BYTES = 200_000

# any path component with these names is blocked (credential stores, keyrings, browser profiles, other AI tools' logins)
DENY_PARTS = {".ssh", ".gnupg", ".aws", ".kube", ".azure", ".docker", ".password-store", ".mozilla", ".thunderbird", ".pki", ".claude", ".codex", ".gemini", ".opencode",
              ".gcloud", ".keyrings", "keyrings", "brave-browser", "braveSoftware", "google-chrome", "chromium", "signal", "slack", "discord", "1password", "bitwarden", ".trash",
              "secrets", "agent-zero"}   # any checkout's secrets folder; Agent Zero's settings hold its tokens (audit 2026-10-20)
# relative path prefixes (from the home folder)
DENY_PREFIXES = ("appdata", ".config", ".local/share",   # AppData: Windows' .config and .local (browser profiles, logins, app tokens) ".local/state", ".var", "snap", ".config/gcloud", ".config/gh", ".config/solana", ".config/opencode", ".config/google-chrome", ".config/chromium", ".config/BraveSoftware", ".config/Signal",
                 ".config/discord", ".config/Slack", ".local/share/keyrings", "snap/firefox", ".cache", ".mozilla")
DENY_NAME = re.compile(r"^(id_(rsa|dsa|ecdsa|ed25519)(\..*)?|.*\.(pem|key|p12|pfx|kdbx|keystore|jks|ppk|gpg|asc)|\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|\.git-credentials|"
                       r"\.(bash|zsh|python|psql|mysql|node_repl)_history|\.lesshst|credentials(\.json)?|.*(token|secret|apikey|api_key)s?.*\.(json|txt|env|ini|yaml|yml)|id\.json|"
                       r"wallet.*|access_code|session_key|google_client\.json|cookies\.sqlite|login data|logins\.json|key[34]\.db|\.claude\.json(\.backup.*)?)$", re.I)
SECRET_PATTERNS = [(re.compile(r"://[^/\s:@]+:[^@\s/]{3,}@"), "://[REDACTED url credentials]@"), (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED google key]"), (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[REDACTED api key]"),
                   (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "[REDACTED github token]"), (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED slack token]"),
                   (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED aws key]"), (re.compile(r"\bya29\.[A-Za-z0-9_\-]{20,}"), "[REDACTED google oauth]"),
                   (re.compile(r"(?i)(?<![A-Za-z0-9])[\w.-]{0,40}(password|passwd|secret|token|api[_-]?key)\w{0,20}\s*[:=]\s*['\"]?[^\s'\"]{8,}"), "[REDACTED credential]"),   # also MY_SERVICE_ACCESS_TOKEN=...
                   (re.compile(r'(?i)"[^"\n]{0,40}(?:password|passwd|secret|token|api[_-]?key|authorization)[^"\n]{0,40}"\s*:\s*"[^"\n]{8,}"'), '"[REDACTED credential]": "[REDACTED]"'),   # JSON keys (bounded)
                   (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"), "Bearer [REDACTED]"),
                   (re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,90}\b"), "[REDACTED possible private key]"),
                   (re.compile(r"\b(?:[a-z]{3,8} ){11}[a-z]{3,8}\b(?=.{0,300}\bseed\b)", re.I), "[REDACTED possible seed phrase]")]   # bounded look-ahead: long lines stay fast
PRIVATE_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


class Blocked(Exception):
    pass


def resolve(path: str) -> Path:
    """Turn what the agent asked for into a real path, or raise Blocked with a plain reason."""
    raw = (path or "~").strip()
    if raw == "~" or raw.startswith("~/"):
        p = HOME / raw[2:]                 # '~' always means the protected home folder (no other user's home)
    else:
        p = Path(raw)
        if not p.is_absolute():
            p = HOME / p
    try:
        real = p.resolve()                                     # follows symlinks: a link cannot be used to escape
    except (OSError, RuntimeError):
        raise Blocked("That path could not be resolved.")
    if real != HOME and HOME not in real.parents:
        raise Blocked("Only files inside the home folder can be read.")
    rel = real.relative_to(HOME)
    parts = [x.lower() for x in rel.parts]
    shipped = ROOT.resolve() / "skills"                       # the skills that come with Rosie: readable even where her folder is
    if (real == shipped or shipped in real.parents) and not (real.name and DENY_NAME.match(real.name)):   # under AppData (Windows)
        return real
    if any(x in {d.lower() for d in DENY_PARTS} for x in parts):
        raise Blocked("That location holds credentials or private app data and is blocked.")
    rels = rel.as_posix().lower()                          # the prefixes use "/"; Windows paths use "\\" (owner 2026-10-23, the Windows copy)
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
            continue                                            # blocked entries are simply not listed
        out.append(e.name + ("/" if e.is_dir() else ""))
        if len(out) >= limit:
            break
    return {"path": str(p), "entries": out}
