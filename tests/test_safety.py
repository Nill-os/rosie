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


def test_windows_credential_places_are_blocked_too(monkeypatch, tmp_path):
    # board #887 safety-windows-appdata-secrets
    monkeypatch.setattr(fsguard, "HOME", tmp_path)
    for p in ("AppData/Local/Google/Chrome/User Data/Default/Login Data", "AppData/Roaming/Microsoft/Credentials/x", ".config/gcloud/credentials.db"):
        with pytest.raises(fsguard.Blocked):
            fsguard.resolve(str(tmp_path / p))
    assert usertools.FORBIDDEN.search(r"open(r'C:\Users\her\AppData\Roaming\Mozilla\key4.db')")
    assert usertools.FORBIDDEN.search(r"open('C:\\rosie\\secrets\\access_code')")


def test_rosies_own_shipped_skills_stay_readable_even_under_appdata(monkeypatch, tmp_path):
    root = tmp_path / "AppData" / "Local" / "Rosie" / "slots" / "A"
    (root / "skills" / "cad").mkdir(parents=True)
    (root / "skills" / "cad" / "SKILL.md").write_text("x")
    (root / "skills" / "cad" / "api_key.json").write_text("x")
    monkeypatch.setattr(fsguard, "HOME", tmp_path)
    monkeypatch.setattr(fsguard, "ROOT", root)
    assert fsguard.resolve(str(root / "skills" / "cad" / "SKILL.md")).name == "SKILL.md"
    with pytest.raises(fsguard.Blocked):
        fsguard.resolve(str(root / "skills" / "cad" / "api_key.json"))
    with pytest.raises(fsguard.Blocked):
        fsguard.resolve(str(root / "app" / "server.py"))


def test_private_key_blocks_basic_auth_cookies_and_jwts_are_redacted():
    key = "-----BEGIN OPENSSH PRIV" + "ATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIV" + "ATE KEY-----"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"
    text = fsguard.redact(f"{key}\nAuthorization: Basic dXNlcjpwYXNzd29yZA==\nCookie: assistant_session=deadbeef12\n{jwt}")[0]
    for secret in ("b3BlbnNzaC1rZXk", "dXNlcjpwYXNzd29yZA", "deadbeef12", jwt, "END OPENSSH"):
        assert secret not in text


def test_the_word_basic_in_ordinary_prose_is_not_redacted():
    for text in ("Basic authentication methods", "basic configuration", "basic\nauthorization", "basic requirements and information"):
        assert fsguard.redact(text)[0] == text
