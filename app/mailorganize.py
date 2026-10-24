"""Mail organizer: files mail under the account's OWN Gmail labels. The triage call (mailwatch) picks at most one allowed label per email;
this module turns those picks into a preview, and applies them only on the owner's OK. Adds labels only (optionally takes the mail out of
the Inbox), keeps an undo, skips starred mail, never trashes, never deletes, never creates or renames a label."""
import json
import time
from pathlib import Path

import config
import gmail
import mailclean
import mailwatch

ROOT = Path(__file__).resolve().parent.parent
UNDO = ROOT / "data" / "mail_organize_undo.json"
MAX_LABELS = 40                             # most labels the owner may allow
SKIP = {"STARRED", "TRASH", "SPAM", "DRAFT"}


def account_labels(name) -> dict:
    """{label name: label id} for the account's own (user) labels. Built-in ones like INBOX never appear."""
    r = gmail.service(name).users().labels().list(userId="me").execute()
    return {l["name"]: l["id"] for l in r.get("labels", []) if l.get("type") == "user"}


def all_labels() -> list[dict]:
    """Every user label across the connected accounts, with whether the owner allowed it and their hint for it."""
    allowed, seen = config.load().get("mail_labels") or {}, {}
    for a in gmail.accounts():
        for n in account_labels(a["name"]):
            seen[n] = {"name": n, "allowed": n in allowed, "hint": str(allowed.get(n, ""))}
    return sorted(seen.values(), key=lambda x: x["name"].lower())


def set_allowed(chosen: dict) -> dict:
    """Save the labels Rosie may use. Only names that really exist on a connected account are kept; the model can never invent one."""
    real = {l["name"] for l in all_labels()}
    clean = {str(n): " ".join(str(h or "").split())[:120] for n, h in (chosen or {}).items() if n in real}
    if len(clean) > MAX_LABELS:
        raise ValueError(f"Pick at most {MAX_LABELS} labels.")
    cfg = config.load()
    cfg["mail_labels"] = clean
    config.save(cfg)
    return clean


def enabled() -> bool:
    cfg = config.load()
    return bool(cfg.get("mail_organize") and cfg.get("mail_labels"))


def choices_for(name) -> dict:
    """The allowed labels this account really has: {name: hint}. Empty when the organizer is off or nothing was picked."""
    cfg = config.load()
    if not enabled():
        return {}
    have = account_labels(name)
    return {n: h for n, h in (cfg.get("mail_labels") or {}).items() if n in have}


def _current(svc, ids) -> dict:
    """{id: set of label ids} as Gmail has them right now (so mail the owner starred or archived since triage is seen)."""
    got = {}
    for i in range(0, len(ids), 50):
        b = svc.new_batch_http_request(callback=lambda rid, resp, exc: got.update({resp["id"]: set(resp.get("labelIds", []))}) if resp else None)
        for m in ids[i:i + 50]:
            b.add(svc.users().messages().get(userId="me", id=m, format="minimal"))
        b.execute()
    return got


def _plan(account="") -> dict:
    """Which mail would get which label: {account, groups: [{label, label_id, rows: [...]}], skipped}. Changes nothing."""
    name = mailclean._acct(account)
    if not name:
        return {"error": "No Gmail account is connected yet. Run ~/assistant/login-gmail.sh"}
    if not enabled():
        return {"error": "Turn on the mail organizer in Settings and pick the labels it may use."}
    have = account_labels(name)
    allowed = config.load().get("mail_labels") or {}
    rows = mailwatch.pending_labels(name)
    rows = [r for r in rows if r["label"] in allowed and r["label"] in have][:mailclean.CAP]
    now = _current(gmail.service(name), [r["id"] for r in rows]) if rows else {}
    groups, skipped = {}, 0
    for r in rows:
        cur = now.get(r["id"])
        if cur is None or cur & SKIP or "INBOX" not in cur or have[r["label"]] in cur:      # gone, starred, already filed, or already out of the Inbox
            skipped += 1
            continue
        g = groups.setdefault(r["label"], {"label": r["label"], "label_id": have[r["label"]], "rows": []})
        g["rows"].append(r)
    return {"account": name, "groups": list(groups.values()), "skipped": skipped}


def preview(account="") -> dict:
    """What WOULD be labeled, grouped by label with counts and subjects. Changes nothing."""
    try:
        p = _plan(account)
    except (gmail.MailLoginExpired, ValueError) as e:
        return {"error": str(e)}
    if "error" in p:
        return p
    groups = [{"label": g["label"], "count": len(g["rows"]), "subjects": [f"{r['sender_name']}: {r['subject'][:70]}" for r in g["rows"][:6]]} for g in p["groups"]]
    return {"account": p["account"], "groups": groups, "total": sum(g["count"] for g in groups), "skipped": p["skipped"], "archive": bool(config.load().get("mail_organize_archive")),
            "can_change_mail": mailclean.can_modify(p["account"]), "note": "Starred mail is always skipped. Nothing has been changed."}


def apply(account="") -> dict:
    """Label what the preview shows. Only called from the Mail page's OK button; the plan is made again here, never trusted from the page."""
    try:
        name = mailclean._acct(account)
        if name and not mailclean.can_modify(name):
            return {"error": mailclean.NEED_LOGIN}
        p = _plan(account)
        if "error" in p:
            return p
        archive = bool(config.load().get("mail_organize_archive"))
        groups, left = [], mailclean.CAP
        for g in p["groups"]:
            ids = [r["id"] for r in g["rows"]][:left]
            left -= len(ids)
            if ids:
                groups.append({"label": g["label"], "label_id": g["label_id"], "ids": ids})
        if not groups:
            return {"status": "nothing", "message": "Nothing to label."}
        UNDO.parent.mkdir(exist_ok=True)
        UNDO.write_text(json.dumps({"account": p["account"], "archived": archive, "groups": groups, "ts": time.time()}))
        UNDO.chmod(0o600)                                      # written first: a failure part way through can still be undone
        svc = gmail.service(p["account"])
        for g in groups:
            for i in range(0, len(g["ids"]), 1000):
                svc.users().messages().batchModify(userId="me", body={"ids": g["ids"][i:i + 1000], "addLabelIds": [g["label_id"]]}).execute()
            if archive:
                for i in range(0, len(g["ids"]), 1000):
                    svc.users().messages().batchModify(userId="me", body={"ids": g["ids"][i:i + 1000], "removeLabelIds": ["INBOX"]}).execute()
        mailwatch.mark_labeled(p["account"], [i for g in groups for i in g["ids"]], "applied")
        return {"status": "done", "changed": sum(len(g["ids"]) for g in groups), "archived": archive, "note": "Undo removes the labels again."}
    except (gmail.MailLoginExpired, ValueError) as e:
        return {"error": str(e)}


def undo() -> dict:
    """Take the labels back off (and put the mail back in the Inbox if it was archived)."""
    try:
        u = json.loads(UNDO.read_text())
    except (OSError, ValueError):
        return {"error": "Nothing to undo."}
    if time.time() - u["ts"] > 7 * 86400:
        return {"error": "That is too old to undo automatically. Remove the labels in Gmail."}
    try:
        svc = gmail.service(u["account"])
        for g in u["groups"]:
            for i in range(0, len(g["ids"]), 1000):
                body = {"ids": g["ids"][i:i + 1000], "removeLabelIds": [g["label_id"]]}
                if u["archived"]:
                    body["addLabelIds"] = ["INBOX"]
                svc.users().messages().batchModify(userId="me", body=body).execute()
    except gmail.MailLoginExpired as e:
        return {"error": str(e)}
    mailwatch.mark_labeled(u["account"], [i for g in u["groups"] for i in g["ids"]], "undone")      # not offered again
    UNDO.unlink(missing_ok=True)
    return {"status": "done", "restored": sum(len(g["ids"]) for g in u["groups"])}
