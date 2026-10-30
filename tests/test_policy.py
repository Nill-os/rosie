import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import actions
import fsguard
import policy
import terminals
import usertools

OLD_EXEC = re.compile(r"(--dangerously[\w-]*|--yolo\b|(?<!\w)-y(?!\w)|--full-auto\b|--auto-approve\b|bypass[-_ ]?permissions|--approval-mode[= ]+yolo|--no-sandbox\b|--sandbox[= ]+danger-full-access)", re.I)
OLD_CODE = re.compile(r"(\.ssh|\.gnupg|\.aws|\.kube|\.password-store|\.mozilla|google-chrome|chromium|keyrings|\.claude|\.codex|\.gemini|\.config[/\\](gcloud|gh)|[/\\]secrets\b|appdata[/\\]|session_key|access_code|google_token|google_client|gmail_\w+\.json|id_rsa|id_ed25519|\.netrc|\.git-credentials|\.env\b|wallet)", re.I)
EXEC_POS = ["claude --dangerously-skip-permissions", "codex --yolo", "apt -y x", "codex --full-auto", "x --auto-approve", "bypass permissions", "gemini --approval-mode yolo", "chrome --no-sandbox", "--sandbox=danger-full-access", "--DANGEROUSLY-x"]
EXEC_NEG = ["ls -la", "git status", "echo a-y-b", "claude --resume"]
CODE_POS = ["~/.ssh/id_rsa", ".env", "wallet.dat", "~/.config/gh/hosts.yml", "C:\\AppData\\x", "google_token", "gmail_a.json", ".claude", "chromium", "/x/secrets/y", ".netrc", "keyrings"]
CODE_NEG = ["print(1)", "environment", "import os"]


def test_self_test_examples_pass():
    assert policy.self_test() == []


def test_old_exec_positive_cases_stay_blocked():
    for s in EXEC_POS:
        assert OLD_EXEC.search(s)
        assert terminals.FORBIDDEN.search(s), s
    for s in EXEC_NEG:
        assert bool(terminals.FORBIDDEN.search(s)) == bool(OLD_EXEC.search(s))


def test_old_code_positive_cases_stay_blocked():
    for s in CODE_POS:
        assert OLD_CODE.search(s)
        assert usertools.FORBIDDEN.search(s), s
    for s in CODE_NEG:
        assert bool(usertools.FORBIDDEN.search(s)) == bool(OLD_CODE.search(s))


def test_old_path_lists_unchanged():
    assert ".ssh" in fsguard.DENY_PARTS and "agent-zero" in fsguard.DENY_PARTS
    assert ".config" in fsguard.DENY_PREFIXES and ".cache" in fsguard.DENY_PREFIXES
    for n in policy.PATH_EXAMPLES_BLOCKED:
        assert fsguard.DENY_NAME.match(n), n


def test_terminal_start_still_refuses(monkeypatch):
    monkeypatch.setattr(terminals, "ensure_server", lambda: None)
    try:
        terminals.start("claude --yolo")
    except ValueError:
        return
    raise AssertionError("not refused")


def test_decide_and_risk():
    assert policy.decide("exec", "rm -rf /tmp/x").decision == policy.PROMPT
    assert policy.decide("exec", "codex --yolo").decision == policy.FORBIDDEN
    assert policy.decide("exec", "ls") is None
    assert policy.risk("exec", "rm -rf x")["level"] == "high"
    assert policy.risk("exec", "hello", "add_event")["level"] == "low"
    assert policy.risk("exec", "", "delete_event")["level"] == "medium"


def test_confirm_card_has_label_and_still_needs_confirm():
    r = actions.queue_tool("delete_event", {"event_id": "1"}, "Delete event: Lunch")
    assert r["status"] == "needs_confirmation"
    assert r["token"] in actions._pending
    assert r["risk"]["level"] in ("low", "medium", "high") and "risk" in r["risk_text"]
    assert r["summary"] == "Delete event: Lunch"
    assert actions._pending[r["token"]]["summary"] == "Delete event: Lunch"


def test_notify_gate():
    t0 = 1000.0
    k = "t1"
    assert not policy.worth_notifying("", k, now=t0)
    assert not policy.worth_notifying("ok", k, now=t0)
    assert not policy.worth_notifying("...........!!!", k, now=t0)
    assert policy.worth_notifying("Your 3pm meeting moved to 4pm", k, now=t0)
    assert not policy.worth_notifying("your 3pm  meeting moved to 4pm", k, now=t0 + 60)
    assert policy.worth_notifying("Your 3pm meeting moved to 4pm", k, now=t0 + 7 * 3600)
    assert policy.worth_notifying("Something different happened today", k, now=t0 + 7 * 3600 + 1)
