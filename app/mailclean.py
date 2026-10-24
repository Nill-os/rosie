"""Gmail cleanup: archive, move to Trash, or mark read, in bulk, with a preview first, your OK every time, and an undo.
Never permanently deletes (Trash keeps mail 30 days), never sends, and never touches starred mail."""
import json
import time
from pathlib import Path

import gmail

ROOT = Path(__file__).resolve().parent.parent
UNDO = ROOT / "data" / "mail_undo.json"
CAP = 500                                   # most messages one cleanup may touch
ACTIONS = {"archive": ([], ["INBOX"]), "trash": (["TRASH"], ["INBOX"]), "mark_read": ([], ["UNREAD"])}      # (labels to add, labels to remove)
NEED_LOGIN = "Mail cleanup needs a one-time re-login that allows changing mail (the old login can only read). Run ~/assistant/login-gmail.sh and approve it."


def _acct(account=""):
    accts = gmail.accounts()
    if not accts:
        return None
    for a in accts:
        if not account or a["name"] == account or a["email"] == account:
            return a["name"]
    return accts[0]["name"]


def can_modify(name) -> bool:
    try:
        return "https://www.googleapis.com/auth/gmail.modify" in json.loads(gmail.token_path(name).read_text()).get("scopes", [])
    except (OSError, ValueError):
        return False


def _ids(name, query):
    svc, ids, tok = gmail.service(name), [], None
    while len(ids) < CAP + 1:
        r = svc.users().messages().list(userId="me", q=query, maxResults=min(500, CAP + 1 - len(ids)), pageToken=tok).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    return ids


ONLY_CHANGED = {"archive": " in:inbox", "mark_read": " is:unread", "trash": " -in:sent -in:drafts"}   # only mail the action really changes (trash: all of it; undo knows which were in the Inbox)


def _query(query, action=""):
    """Only messages the action would change, so undo puts back exactly those (audit 2026-10-20: archiving promotions also matched
    long-archived mail, and undo dumped it all into the Inbox)."""
    q = (query or "").strip()
    if len(q) < 3:
        raise ValueError("Tell me what to clean up, e.g. 'category:promotions older_than:90d' or 'from:newsletter@x.com'.")
    return f"({q}) -is:starred -in:trash -in:spam" + ONLY_CHANGED.get(action, "")


def preview(query, account="", action=""):
    """What WOULD be affected: how many and who from. Changes nothing."""
    name = _acct(account)
    if not name:
        return {"error": "No Gmail account is connected yet. Run ~/assistant/login-gmail.sh"}
    try:
        ids = _ids(name, _query(query, action))
        sample, froms = ids[:30], {}
        if sample:
            svc, got = gmail.service(name), []
            b = svc.new_batch_http_request(callback=lambda rid, resp, exc: got.append(resp) if resp else None)
            for i in sample:
                b.add(svc.users().messages().get(userId="me", id=i, format="metadata", metadataHeaders=["From", "Subject"]))
            b.execute()
            for m in got:
                h = {x["name"].lower(): x["value"] for x in m.get("payload", {}).get("headers", [])}
                s = h.get("from", "?").split("<")[0].strip(' "') or h.get("from", "?")
                froms[s] = froms.get(s, 0) + 1
        return {"matches": f"{CAP}+" if len(ids) > CAP else len(ids), "top_senders_in_sample": dict(sorted(froms.items(), key=lambda kv: -kv[1])[:8]),
                "note": "Starred mail is always skipped. Nothing has been changed.", "can_change_mail": can_modify(name), "query": query}
    except (gmail.MailLoginExpired, ValueError) as e:
        return {"error": str(e)}


def cleanup(action, query, account="", _confirmed=False):
    """archive / trash / mark_read everything matching the search. Always waits for the user's OK (the Confirm card shows how many)."""
    import actions
    if action not in ACTIONS:
        return {"error": "action must be archive, trash or mark_read"}
    name = _acct(account)
    if not name:
        return {"error": "No Gmail account is connected yet. Run ~/assistant/login-gmail.sh"}
    try:
        if not can_modify(name):
            return {"error": NEED_LOGIN}
        if action not in ACTIONS:
            return {"error": f"action must be one of: {', '.join(ACTIONS)}"}
        ids = _ids(name, _query(query, action))
        if not ids:
            return {"status": "nothing", "message": "Nothing matches that."}
        ids = ids[:CAP]
        if not _confirmed:
            what = {"archive": "Archive (remove from Inbox, keep in All Mail)", "trash": "Move to Trash (recoverable for 30 days)", "mark_read": "Mark as read"}[action]
            n = f"{CAP}+ (I do {CAP} at a time)" if len(ids) >= CAP else str(len(ids))
            return actions.queue_tool("mail_cleanup", {"action": action, "query": query, "account": name, "_confirmed": True},
                                      f"{what}: {n} emails matching '{query}' in {name}. Starred mail is skipped. I can undo this right after.")
        add, remove = ACTIONS[action]
        svc = gmail.service(name)
        inbox_ids = sorted(set(ids) & set(_ids(name, _query(query, action) + " in:inbox"))) if action == "trash" else ids   # before the move
        UNDO.parent.mkdir(exist_ok=True)
        UNDO.write_text(json.dumps({"account": name, "action": action, "ids": ids, "inbox_ids": inbox_ids, "query": query, "ts": time.time()}))
        UNDO.chmod(0o600)                                     # written first: a failure part way through can still be undone
        for i in range(0, len(ids), 1000):
            svc.users().messages().batchModify(userId="me", body={"ids": ids[i:i + 1000], "addLabelIds": add, "removeLabelIds": remove}).execute()
        return {"status": "done", "changed": len(ids), "action": action, "note": "Say 'undo that' to reverse it."}
    except (gmail.MailLoginExpired, ValueError) as e:
        return {"error": str(e)}


def undo():
    """Reverse the most recent cleanup."""
    try:
        u = json.loads(UNDO.read_text())
    except (OSError, ValueError):
        return {"error": "Nothing to undo."}
    if time.time() - u["ts"] > 7 * 86400:
        return {"error": "That cleanup is too old to undo automatically. Look in Gmail's Trash or All Mail."}
    add, remove = ACTIONS[u["action"]]
    try:
        svc = gmail.service(u["account"])
        if u["action"] == "trash":                             # out of Trash for all; back into the Inbox only those that were there
            for i in range(0, len(u["ids"]), 1000):
                svc.users().messages().batchModify(userId="me", body={"ids": u["ids"][i:i + 1000], "addLabelIds": [], "removeLabelIds": ["TRASH"]}).execute()
            back = u.get("inbox_ids", u["ids"])
            for i in range(0, len(back), 1000):
                svc.users().messages().batchModify(userId="me", body={"ids": back[i:i + 1000], "addLabelIds": ["INBOX"], "removeLabelIds": []}).execute()
        else:
            for i in range(0, len(u["ids"]), 1000):
                svc.users().messages().batchModify(userId="me", body={"ids": u["ids"][i:i + 1000], "addLabelIds": remove, "removeLabelIds": add}).execute()
    except gmail.MailLoginExpired as e:
        return {"error": str(e)}
    UNDO.unlink(missing_ok=True)
    return {"status": "done", "restored": len(u["ids"]), "action_reversed": u["action"]}
