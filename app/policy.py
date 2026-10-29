import re
import time
from dataclasses import dataclass, field

ALLOW, PROMPT, FORBIDDEN = "allow", "prompt", "forbidden"
LEVELS = {ALLOW: "low", PROMPT: "medium", FORBIDDEN: "high"}


@dataclass(frozen=True)
class Rule:
    domain: str
    name: str
    pattern: re.Pattern
    decision: str
    why: str
    match: tuple = ()
    nomatch: tuple = ()
    level: str = ""


def _r(domain, name, rx, decision, why, match=(), nomatch=(), level="", flags=re.I):
    return Rule(domain, name, re.compile(rx, flags), decision, why, tuple(match), tuple(nomatch), level or LEVELS[decision])


RULES = [
    _r("exec", "agent-skip-permissions", r"(--dangerously[\w-]*|--yolo\b|(?<!\w)-y(?!\w)|--full-auto\b|--auto-approve\b|bypass[-_ ]?permissions|--approval-mode[= ]+yolo|--no-sandbox\b|--sandbox[= ]+danger-full-access)",
       FORBIDDEN, "Turns off an agent's permission checks or sandbox.",
       ("claude --dangerously-skip-permissions", "codex --yolo", "apt install -y foo", "codex --full-auto", "gemini --approval-mode=yolo", "chrome --no-sandbox", "x bypass_permissions", "--auto-approve"),
       ("claude --resume", "ls -la", "echo my-y-file", "git status")),
    _r("exec", "recursive-delete", r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*|--recursive)\b", PROMPT, "Deletes whole folders without a trash step.", ("rm -rf build", "rm -fr x"), ("rm file.txt", "echo firmware"), "high"),
    _r("exec", "privilege-escalation", r"(^|[;&|]\s*)sudo\b", PROMPT, "Runs with administrator rights.", ("sudo apt update", "ls; sudo reboot"), ("echo pseudo", "visudo"), "high"),
    _r("exec", "pipe-to-shell", r"\|\s*(sudo\s+)?(ba|z)?sh\b", PROMPT, "Runs downloaded text as a program.", ("curl x.sh | sh", "wget -O- u | sudo bash"), ("curl u | grep sh", "echo shell"), "high"),
    _r("exec", "force-push", r"\bgit\s+push\b.*(--force\b|\s-f\b)", PROMPT, "Overwrites history on a remote.", ("git push --force origin main", "git push -f"), ("git push origin main", "git pull"), "high"),
    _r("exec", "network-fetch", r"\b(curl|wget)\b", PROMPT, "Fetches or sends data over the network.", ("curl http://x", "wget u"), ("ls", "echo scurly"), "medium"),
    _r("code", "credential-store",
       r"(\.ssh|\.gnupg|\.aws|\.kube|\.password-store|\.mozilla|google-chrome|chromium|keyrings|\.claude|\.codex|\.gemini|\.config[/\\](gcloud|gh)|[/\\]secrets\b|appdata[/\\]|session_key|access_code|google_token|google_client|gmail_\w+\.json|id_rsa|id_ed25519|\.netrc|\.git-credentials|\.env\b|wallet)",
       FORBIDDEN, "Touches a credential store or secret.",
       ("open('/home/u/.ssh/id_rsa')", "x = '.env'", "Wallet", "~/.config/gh/hosts.yml", "path/secrets/x", "AppData\\Roaming", "gmail_token.json"),
       ("print('hello')", "import json", "environment")),
]

DENY_PARTS = {".ssh", ".gnupg", ".aws", ".kube", ".azure", ".docker", ".password-store", ".mozilla", ".thunderbird", ".pki", ".claude", ".codex", ".gemini", ".opencode",
              ".gcloud", ".keyrings", "keyrings", "brave-browser", "braveSoftware", "google-chrome", "chromium", "signal", "slack", "discord", "1password", "bitwarden", ".trash",
              "secrets", "agent-zero"}
DENY_PREFIXES = ("appdata", ".config", ".local/share",
                 ".config/discord", ".config/Slack", ".local/share/keyrings", "snap/firefox", ".cache", ".mozilla")
DENY_NAME = re.compile(r"^(id_(rsa|dsa|ecdsa|ed25519)(\..*)?|.*\.(pem|key|p12|pfx|kdbx|keystore|jks|ppk|gpg|asc)|\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|\.git-credentials|"
                       r"\.(bash|zsh|python|psql|mysql|node_repl)_history|\.lesshst|credentials(\.json)?|.*(token|secret|apikey|api_key)s?.*\.(json|txt|env|ini|yaml|yml)|id\.json|"
                       r"wallet.*|access_code|session_key|google_client\.json|cookies\.sqlite|login data|logins\.json|key[34]\.db|\.claude\.json(\.backup.*)?)$", re.I)
PATH_EXAMPLES_BLOCKED = ("id_rsa", "id_ed25519.pub", "server.pem", "a.key", ".env", ".env.local", ".netrc", ".npmrc", ".bash_history", "credentials.json", "my_token.json", "wallet.dat",
                         "access_code", "session_key", "google_client.json", "cookies.sqlite", "Login Data", "logins.json", "key4.db", ".claude.json")
PATH_EXAMPLES_OK = ("notes.txt", "readme.md", "keyboard.txt", "main.py")


class _Matcher:
    def __init__(self, domain):
        self.domain = domain

    def search(self, text):
        return check(self.domain, text, FORBIDDEN)


def matcher(domain):
    return _Matcher(domain)


def check(domain, text, decision=None):
    text = text if isinstance(text, str) else str(text)
    for r in RULES:
        if r.domain == domain and (decision is None or r.decision == decision):
            m = r.pattern.search(text)
            if m:
                return m
    return None


def decide(domain, text):
    best = None
    for r in RULES:
        if r.domain == domain and r.pattern.search(text if isinstance(text, str) else str(text)):
            if r.decision == FORBIDDEN:
                return r
            if r.decision == PROMPT and (best is None or (r.level == "high" and best.level != "high")):
                best = r
    return best


def path_decision(name):
    return FORBIDDEN if DENY_NAME.match(name or "") else ALLOW


def risk(domain, text="", tool=""):
    hay = f"{tool} {text}"
    r = decide(domain, hay) if domain in {x.domain for x in RULES} else None
    if r:
        return {"level": r.level, "reason": r.why}
    t = (tool or "").lower()
    if any(k in t for k in ("delete", "remove", "shift", "move")):
        return {"level": "medium", "reason": "Changes or removes calendar items."}
    if any(k in t for k in ("send", "email", "post")):
        return {"level": "medium", "reason": "Sends something to other people."}
    return {"level": "low", "reason": "No risky pattern found."}


def risk_text(domain, text="", tool=""):
    k = risk(domain, text, tool)
    return f"Risk (advice only, your confirm is still needed): {k['level']} - {k['reason']}"


def self_test():
    bad = []
    for r in RULES:
        for s in r.match:
            if not r.pattern.search(s):
                bad.append((r.name, "match", s))
        for s in r.nomatch:
            if r.pattern.search(s):
                bad.append((r.name, "nomatch", s))
    for s in PATH_EXAMPLES_BLOCKED:
        if path_decision(s) != FORBIDDEN:
            bad.append(("deny-name", "match", s))
    for s in PATH_EXAMPLES_OK:
        if path_decision(s) != ALLOW:
            bad.append(("deny-name", "nomatch", s))
    return bad


_last_notice: dict = {}


def worth_notifying(text, key="default", min_chars=12, window_hours=6, now=None):
    t = " ".join((text or "").split())
    if len(t) < min_chars or not re.search(r"\w", t):
        return False
    now = time.time() if now is None else now
    last = _last_notice.get(key)
    if last and last[0] == t.lower() and now - last[1] < window_hours * 3600:
        return False
    _last_notice[key] = (t.lower(), now)
    return True
