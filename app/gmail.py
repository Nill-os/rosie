"""Gmail. Permission requested: gmail.modify = read, label, archive, move to Trash and send. Rosie sends exactly one thing: the morning paper, to your
OWN address(es) only (send_own refuses anyone else). It never permanently deletes. Old read-only logins keep working for reading (they can't send)."""
import base64
import json
import re
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config
import web

ROOT = Path(__file__).resolve().parent.parent
CLIENT_FILE = ROOT / "secrets" / "google_client.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
READ_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class MailLoginExpired(Exception):
    """Gmail login is missing or expired; run ./login-gmail.sh"""


def _safe(name):
    return re.sub(r"[^a-z0-9_-]", "", name.lower()) or "main"


def token_path(name) -> Path:
    return ROOT / "secrets" / f"gmail_{_safe(name)}.json"


def accounts() -> list[dict]:
    return [a for a in config.load().get("gmail_accounts", []) if token_path(a["name"]).exists()]


def login(name="main"):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    tp = token_path(name)
    tp.write_text(creds.to_json())
    tp.chmod(0o600)
    try:
        import health
        health.record_login(f"gmail:{_safe(name)}")
    except Exception:
        pass
    email = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute()["emailAddress"]
    cfg = config.load()
    accts = [a for a in cfg.get("gmail_accounts", []) if a["name"] != _safe(name)] + [{"name": _safe(name), "email": email}]
    cfg["gmail_accounts"] = accts
    config.save(cfg)
    return email


def disconnect(name):
    token_path(name).unlink(missing_ok=True)
    cfg = config.load()
    cfg["gmail_accounts"] = [a for a in cfg.get("gmail_accounts", []) if a["name"] != _safe(name)]
    config.save(cfg)


def service(name):
    tp = token_path(name)
    if not tp.exists():
        raise MailLoginExpired(f"Gmail account '{name}' is not connected. Run ~/assistant/login-gmail.sh {name}")
    granted = json.loads(tp.read_text()).get("scopes") or READ_SCOPES
    creds = Credentials.from_authorized_user_file(str(tp), granted)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise MailLoginExpired(f"Gmail login for '{name}' expired (about weekly). Run ~/assistant/login-gmail.sh {name}") from e
        tp.write_text(creds.to_json())
        tp.chmod(0o600)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk(part, out):
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if part.get("filename"):
        out["files"].append(part["filename"])
    elif data and mime in ("text/plain", "text/html"):
        txt = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
        out["plain" if mime == "text/plain" else "html"].append(txt)
    for sub in part.get("parts", []) or []:
        _walk(sub, out)


def parse_message(m: dict) -> dict:
    h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
    out = {"plain": [], "html": [], "files": []}
    _walk(m.get("payload", {}), out)
    body = "\n".join(out["plain"]) or web._text("\n".join(out["html"]))
    return {"id": m["id"], "thread_id": m.get("threadId", m["id"]), "from": h.get("from", ""), "to": h.get("to", ""), "subject": h.get("subject", "(no subject)"),
            "date": h.get("date", ""), "ts": int(m.get("internalDate", 0)) // 1000, "snippet": m.get("snippet", ""), "labels": m.get("labelIds", []),
            "bulk": "list-unsubscribe" in h, "attachments": out["files"][:6], "body": re.sub(r"\n{3,}", "\n\n", body).strip()[:6000]}


def get(name, msg_id) -> dict:
    return parse_message(service(name).users().messages().get(userId="me", id=msg_id, format="full").execute())


def list_ids(name, query="in:inbox newer_than:2d", max_results=40) -> list[str]:
    r = service(name).users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    return [x["id"] for x in r.get("messages", [])]


def link(email, thread_id):
    return f"https://mail.google.com/mail/u/{email}/#inbox/{thread_id}"


def get_meta(name, msg_id) -> dict:
    m = service(name).users().messages().get(userId="me", id=msg_id, format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
    h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
    return {"id": m["id"], "from": h.get("from", ""), "subject": h.get("subject", "(no subject)"), "date": h.get("date", ""), "snippet": m.get("snippet", ""),
            "thread_id": m.get("threadId", m["id"]), "ts": int(m.get("internalDate", 0)) // 1000}


# ---------- sending: your own address only ----------
class NotYourAddress(ValueError):
    """A recipient that isn't the account's own address or one the owner listed for the morning paper."""


def own_addresses(name) -> set[str]:
    """The only addresses Rosie may send to: this Gmail account's own address, plus the extra ones YOU listed in the morning paper's
    settings (e.g. another address of yours). Read fresh from the config every time."""
    cfg = config.load()
    own = {a["email"].strip().lower() for a in cfg.get("gmail_accounts", []) if a.get("name") == _safe(name) and a.get("email")}
    extra = (cfg.get("paper") or {}).get("email_to") if isinstance(cfg.get("paper"), dict) else []
    return own | {str(e).strip().lower() for e in (extra or []) if isinstance(e, str) and re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,24}", str(e).strip().lower())}


def send_own(name, to: list[str], subject: str, html_body: str, text_body: str, attachments=(), service_fn=None) -> str:
    """Send one message from account `name` to your own address(es). Hard rule: every recipient must be in own_addresses(name); no Cc,
    no Bcc, no reply-to. Returns Gmail's message id."""
    from email.message import EmailMessage
    allowed = own_addresses(name)
    rcpt = [str(t).strip().lower() for t in (to or [])]
    if not rcpt or not allowed or any(t not in allowed for t in rcpt):
        raise NotYourAddress("Rosie only emails your own address (the connected Gmail, or ones you added in the paper's settings).")
    sender = next(a["email"] for a in config.load().get("gmail_accounts", []) if a.get("name") == _safe(name))
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(rcpt), re.sub(r"[\r\n]+", " ", subject)[:200]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for fname, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=re.sub(r"[^\w.-]", "_", fname)[:80])
    for hdr in ("Cc", "Bcc", "Reply-To"):
        del msg[hdr]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        r = (service_fn or service)(name).users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        if "insufficient" in str(e).lower() or "403" in str(e):
            raise RuntimeError(f"Your Gmail login can't send mail (it's read-only). Run ~/assistant/login-gmail.sh {_safe(name)} once to allow it.") from e
        raise
    return r.get("id", "")
