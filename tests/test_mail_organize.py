"""The mail organizer (own Gmail labels), calendar requests from email, the Mail page's action list, and the one-time move of
email-made tasks out of Tasks. Gmail and the calendar are fakes: nothing here can touch a real account."""
import datetime as dt
import json
import time

import pytest

import config
import gcal
import gmail
import inbox
import mailclean
import mailorganize
import mailwatch
import proposals
import tasks
import workflows

LABELS = {"Bills": "LBL_1", "Orders": "LBL_2", "Family": "LBL_3"}


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class Batch:
    def __init__(self, callback):
        self.cb, self.reqs = callback, []

    def add(self, req):
        self.reqs.append(req)

    def execute(self):
        for i, r in enumerate(self.reqs):
            self.cb(str(i), r.execute(), None)


class FakeGmail:
    """users().labels().list, users().messages().get / batchModify and batch requests, over a dict {message id: set of label ids}."""
    def __init__(self, msgs):
        self.msgs, self.mods = msgs, []

    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def list(self, userId):
        return Req(lambda: {"labels": [{"id": v, "name": k, "type": "user"} for k, v in LABELS.items()] + [{"id": "INBOX", "name": "INBOX", "type": "system"}]})

    def get(self, userId, id, format):
        return Req(lambda: {"id": id, "labelIds": sorted(self.msgs[id])} if id in self.msgs else {})

    def new_batch_http_request(self, callback):
        return Batch(callback)

    def batchModify(self, userId, body):
        def run():
            self.mods.append(body)
            for i in body["ids"]:
                self.msgs[i] = (self.msgs[i] | set(body.get("addLabelIds", []))) - set(body.get("removeLabelIds", []))
        return Req(run)


@pytest.fixture
def gm(monkeypatch):
    fake = FakeGmail({})
    monkeypatch.setattr(gmail, "accounts", lambda: [{"name": "main", "email": "me@example.com"}])
    monkeypatch.setattr(gmail, "service", lambda name: fake)
    monkeypatch.setattr(mailclean, "can_modify", lambda name: True)
    return fake


def settings(**kw):
    cfg = config.load()
    cfg.update({"mail_organize": True, "mail_labels": {"Bills": "money owed", "Orders": ""}}, **kw)
    config.save(cfg)


def stored(mid, label, subject="Hello", ts=None):
    msg = {"id": mid, "thread_id": "t" + mid, "from": "Acme <a@example.com>", "subject": subject, "date": "", "ts": ts or int(time.time()), "snippet": "s"}
    mailwatch._store("main", msg, {"importance": "normal", "needs_reply": False, "reason": "", "summary": "", "suspicious": False, "label": label})


# ---------- the label choice ----------
def test_the_label_can_only_be_an_allowed_one_or_none(monkeypatch):
    seen = {}

    def fake(system, user, schema, **k):
        seen.update(system=system, schema=schema)
        return {"importance": "normal", "needs_reply": False, "reason": "r", "summary": "s", "tasks": [], "events": [], "label": seen["answer"]}
    monkeypatch.setattr(workflows, "llm_json", fake)
    msg = {"subject": "x", "body": "b", "from": "a@b.c", "date": "", "snippet": "", "attachments": [], "labels": []}
    seen["answer"] = "Orders"
    t = mailwatch.triage(msg, {"Orders": "things I bought", "Bills": ""})
    assert seen["schema"]["properties"]["label"]["enum"] == ["Orders", "Bills", "none"]
    assert "things I bought" in seen["system"] and t["label"] == "Orders"
    seen["answer"] = "Secret stuff"                              # the model invented one: dropped
    assert mailwatch.triage(msg, {"Orders": ""})["label"] == ""
    seen["answer"] = "none"
    assert mailwatch.triage(msg, {"Orders": ""})["label"] == ""
    assert "label" not in mailwatch.schema_for([])["properties"]          # organizer off: the shape is unchanged


def test_nothing_is_chosen_until_the_owner_picks_labels(gm):
    cfg = config.load()
    cfg["mail_organize"] = True
    config.save(cfg)
    assert not mailorganize.enabled() and mailorganize.choices_for("main") == {}
    assert "error" in mailorganize.preview()
    mailorganize.set_allowed({"Bills": "x", "Nope": "not a real label"})
    assert config.load()["mail_labels"] == {"Bills": "x"}               # only labels the account really has
    assert mailorganize.choices_for("main") == {"Bills": "x"}


# ---------- preview, apply, undo ----------
def test_preview_changes_nothing(gm):
    settings()
    gm.msgs.update({"a": {"INBOX"}, "b": {"INBOX"}})
    stored("a", "Bills", "Water bill")
    stored("b", "Orders", "Shipped")
    p = mailorganize.preview()
    assert p["total"] == 2 and {g["label"]: g["count"] for g in p["groups"]} == {"Bills": 1, "Orders": 1}
    assert "Acme: Water bill" in p["groups"][0]["subjects"][0] or "Acme: Water bill" in p["groups"][1]["subjects"][0]
    assert gm.mods == [] and gm.msgs["a"] == {"INBOX"} and not mailorganize.UNDO.exists()


def test_apply_adds_only_allowed_labels_and_skips_starred(gm):
    settings()
    gm.msgs.update({"a": {"INBOX"}, "star": {"INBOX", "STARRED"}, "fam": {"INBOX"}, "gone": {"INBOX"}})
    stored("a", "Bills")
    stored("star", "Bills")
    stored("fam", "Family")                                    # a real label, but the owner did not allow it
    r = mailorganize.apply()
    assert r["status"] == "done" and r["changed"] == 1
    assert gm.msgs["a"] == {"INBOX", "LBL_1"}                   # labeled, still in the Inbox
    assert gm.msgs["star"] == {"INBOX", "STARRED"} and gm.msgs["fam"] == {"INBOX"}
    assert all(set(m) == {"ids", "addLabelIds"} for m in gm.mods)    # add only: never remove, trash or delete
    assert "TRASH" not in json.dumps(gm.mods)
    assert mailorganize.apply().get("status") == "nothing"     # not labeled twice


def test_apply_respects_the_cap(gm, monkeypatch):
    settings()
    monkeypatch.setattr(mailclean, "CAP", 2)
    for i in "abc":
        gm.msgs[i] = {"INBOX"}
        stored(i, "Bills")
    assert mailorganize.apply()["changed"] == 2
    assert sum("LBL_1" in v for v in gm.msgs.values()) == 2


def test_undo_removes_the_labels(gm):
    settings()
    gm.msgs.update({"a": {"INBOX"}})
    stored("a", "Bills")
    mailorganize.apply()
    assert mailorganize.undo()["restored"] == 1
    assert gm.msgs["a"] == {"INBOX"} and not mailorganize.UNDO.exists()
    assert mailorganize.preview()["total"] == 0                 # an undone choice is not offered again


def test_archive_is_off_by_default_and_undo_puts_the_inbox_back(gm):
    settings()
    gm.msgs.update({"a": {"INBOX"}})
    stored("a", "Bills")
    mailorganize.apply()
    assert "INBOX" in gm.msgs["a"]
    mailorganize.undo()
    settings(mail_organize_archive=True)
    gm.msgs["b"] = {"INBOX"}
    stored("b", "Orders")
    assert mailorganize.preview()["archive"] is True
    mailorganize.apply()
    assert gm.msgs["b"] == {"LBL_2"}                           # labeled and out of the Inbox
    mailorganize.undo()
    assert gm.msgs["b"] == {"INBOX"}


def test_apply_needs_the_modify_login(gm, monkeypatch):
    settings()
    monkeypatch.setattr(mailclean, "can_modify", lambda name: False)
    gm.msgs["a"] = {"INBOX"}
    stored("a", "Bills")
    assert mailorganize.apply()["error"] == mailclean.NEED_LOGIN and gm.mods == []


# ---------- appointments become calendar requests; to-dos go to the Mail page ----------
def _msg():
    return {"id": "m1", "from": "Clinic <c@example.com>", "subject": "Visit", "ts": int(time.time())}


def _pending(kind=None):
    return [p for p in proposals.list_("pending") if kind in (None, p["kind"])]


def test_a_time_makes_a_calendar_request_and_says_if_it_overlaps(monkeypatch):
    monkeypatch.setattr(inbox, "parse_when", lambda w, now=None: dt.datetime(2026, 11, 2, 15, 0, tzinfo=gcal.TZ))
    monkeypatch.setattr(gcal, "conflicts", lambda s, e: [{"title": "Class", "start": "", "end": ""}])
    mailwatch._propose("main", _msg(), {"tasks": [], "events": [{"title": "Dentist", "when": "monday 3pm"}]}, {})
    p = _pending("event")[0]
    assert p["title"] == "Add to calendar: Dentist — Mon Nov 2 3:00 PM"
    assert "Overlaps: Class" in p["detail"] and p["payload"]["start"].startswith("2026-11-02T15:00")


def test_a_day_without_a_time_is_an_all_day_request(monkeypatch):
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    mailwatch._propose("main", _msg(), {"tasks": [], "events": [{"title": "Picnic", "when": day}]}, {})
    p = _pending("event")[0]
    assert p["payload"] == {"title": "Picnic", "all_day": True, "day": day} and "(all day)" in p["title"]
    assert _pending("task") == []
    added = []
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: [])
    monkeypatch.setattr(gcal, "write_calendar_id", lambda: "cal")
    monkeypatch.setattr(gcal, "add_all_day_event", lambda title, d: added.append((title, d)) or {"id": "e"})
    assert proposals.approve(p["id"])["status"] == "approved"
    assert added == [("Picnic", dt.date.fromisoformat(day))]


def test_no_date_goes_to_the_mail_action_list_not_tasks():
    mailwatch._propose("main", _msg(), {"tasks": [{"title": "Send the form", "when": ""}], "events": [{"title": "Call back", "when": "sometime soon"}]}, {})
    assert _pending() == [] and tasks.upcoming() == [] and tasks.overdue() == [] and tasks.list_for() == []
    got = {t["title"]: t for t in mailwatch.todos()}
    assert got["Call back"]["note"] == "No date found" and got["Send the form"]["due"] == ""
    mailwatch._propose("main", _msg(), {"tasks": [{"title": "Send the form", "when": ""}], "events": []}, {})   # not repeated
    assert len(mailwatch.todos()) == 2
    mailwatch.todo_done(got["Call back"]["id"])
    assert [t["title"] for t in mailwatch.todos()] == ["Send the form"]


def test_a_task_with_a_day_keeps_its_due_date_on_the_action_list():
    mailwatch._propose("main", _msg(), {"tasks": [{"title": "Pay it", "when": "tomorrow"}], "events": []}, {})
    assert mailwatch.todos()[0]["due"] == (dt.datetime.fromtimestamp(_msg()["ts"]).date() + dt.timedelta(days=1)).isoformat()


# ---------- the one-time move out of Tasks ----------
def _approved_email_task(title, mid):
    pid = proposals.add("task", title, {"title": title, "due": ""}, source=f"email:main:{mid}", key=f"k:{mid}")
    proposals.approve(pid)
    return pid


def test_migration_moves_only_matched_email_tasks_and_backs_them_up(tmp_path):
    _approved_email_task("Renew passport", "m1")                 # recorded by id
    old = _approved_email_task("Return the form", "m2")
    with proposals._db() as c:
        c.execute("UPDATE proposals SET ref='' WHERE id=?", (old,))     # an older approval that recorded nothing: matched by title and time
    done_pid = _approved_email_task("Already finished", "m3")
    tasks.complete(next(t["id"] for t in tasks.upcoming() + tasks.list_for() if t["title"] == "Already finished"))
    vet = _approved_email_task("Call the vet", "m4")
    with proposals._db() as c:
        c.execute("UPDATE proposals SET ref='' WHERE id=?", (vet,))
    tasks.add("Call the vet")                                    # no recorded id and the owner has the same title: not certain, stays
    own = tasks.add("Buy milk")
    meeting = tasks.add("Follow up from standup meeting")
    r = mailwatch.migrate_email_tasks()
    assert r["moved"] == 2
    left = {t["title"] for t in tasks.list_for() + tasks.upcoming() + tasks.overdue()}
    assert "Renew passport" not in left and "Return the form" not in left
    assert {"Call the vet", "Buy milk", "Follow up from standup meeting"} <= left
    assert tasks.get(own["id"]) and tasks.get(meeting["id"]) and done_pid
    assert {t["title"] for t in mailwatch.todos()} == {"Renew passport", "Return the form"}
    backup = list(mailwatch.BACKUPS.glob("email_tasks_*.json"))
    assert len(backup) == 1 and {x["task"]["title"] for x in json.loads(backup[0].read_text())} == {"Renew passport", "Return the form"}
    assert mailwatch.migrate_email_tasks()["already"] and len(list(mailwatch.BACKUPS.glob("*.json"))) == 1


def test_migration_with_nothing_to_move_writes_no_backup():
    tasks.add("Only mine")
    assert mailwatch.migrate_email_tasks()["moved"] == 0
    assert not mailwatch.BACKUPS.exists() or not list(mailwatch.BACKUPS.glob("*.json"))
    assert [t["title"] for t in tasks.list_for()] == ["Only mine"]
