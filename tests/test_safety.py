import pytest

import fsguard
import mailclean
import usertools


def test_credential_stores_are_blocked():
    for p in ("~/.ssh/id_rsa", "~/.config/gcloud/credentials.db", "~/.gnupg/pubring.kbx", "~/assistant/secrets/session_key"):
        with pytest.raises(Exception):
            fsguard.resolve(p)


def test_secrets_are_redacted():
    out = fsguard.redact("token = abcdefghijklmnop1234 and AIza" + "x" * 35)
    text = out[0] if isinstance(out, tuple) else out
    assert "abcdefghijklmnop1234" not in text and "AIza" + "x" * 35 not in text


def test_tools_cannot_touch_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(usertools, "DIR", tmp_path / "tools")
    monkeypatch.setattr(usertools, "SITE", tmp_path / "tools" / "site")
    assert "Refused" in usertools.build_tool("steal", "x", "open('/home/u/.ssh/id_rsa').read()")["error"]
    assert usertools.build_tool("ok_tool", "x", "import sys\nprint(sys.argv[1].upper())")["status"] == "saved"
    r = usertools.run_tool("ok_tool", "hello")
    assert r["exit_code"] == 0 and r["output"].strip() == "HELLO"
    assert "Tool name" in usertools.build_tool("Bad Name!", "x", "print(1)")["error"]


def test_mail_cleanup_protects_starred_and_needs_a_real_query():
    assert "-is:starred" in mailclean._query("category:promotions older_than:90d")
    with pytest.raises(ValueError):
        mailclean._query("  ")


def test_mail_cleanup_actions_never_delete_permanently():
    assert set(mailclean.ACTIONS) == {"archive", "trash", "mark_read"}
