import datetime as dt
import re
import sqlite3
import subprocess
from pathlib import Path

import tasks
import workflows

DB = Path(__file__).resolve().parent.parent / "data" / "projects.db"
STATUSES = ("idea", "active", "paused", "done")
FIELDS = {"name", "area", "goal", "finish_line", "next_action", "status", "folder"}
SKIP = {"node_modules", "venv", ".venv", "site-packages", "snap", "go", "build", "dist", "__pycache__"}


def _db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, area TEXT NOT NULL DEFAULT '',
                 goal TEXT NOT NULL DEFAULT '', finish_line TEXT NOT NULL DEFAULT '', next_action TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                 folder TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS milestones (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                 title TEXT NOT NULL, due TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0)""")
    return c


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _slug(name):
    return re.sub(r"[^\w\- ]", "", name).strip()[:60] or "project"


def notes_path(name) -> Path:
    return workflows.VAULT / "Projects" / f"{_slug(name)}.md"


def _ensure_notes(p):
    f = notes_path(p["name"])
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {p['name']}\n\n**Goal:** {p['goal']}\n\n**Done when:** {p['finish_line']}\n\n## Log\n\n## Ideas\n\n## Decisions\n")


def _row(r):
    return dict(r) if r else None


def get(ref):
    with _db() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (ref,)).fetchone() if str(ref).isdigit() else \
            c.execute("SELECT * FROM projects WHERE lower(name)=lower(?)", (str(ref).strip(),)).fetchone() or \
            c.execute("SELECT * FROM projects WHERE lower(name) LIKE lower(?) ORDER BY length(name) LIMIT 1", (f"%{str(ref).strip()}%",)).fetchone()
        return _row(r)


def create(name, goal="", finish_line="", folder="", area="", status="active", next_action=""):
    name = " ".join(name.split())[:80]
    if not name:
        raise ValueError("A project needs a name.")
    if status not in STATUSES:
        raise ValueError("Status must be idea, active, paused or done.")
    if folder:
        p = Path(folder).expanduser().resolve()
        if not p.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        folder = str(p)
    with _db() as c:
        try:
            cur = c.execute("INSERT INTO projects(name,area,goal,finish_line,next_action,status,folder,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                            (name, area[:40], goal[:600], finish_line[:300], next_action[:200], status, folder, _now(), _now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"A project named '{name}' already exists.")
        pid = cur.lastrowid
    p = get(pid)
    _ensure_notes(p)
    return p


def update(ref, **fields):
    p = get(ref)
    if not p:
        raise ValueError("No such project.")
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"Can't change: {', '.join(sorted(bad))}")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError("Status must be idea, active, paused or done.")
    if fields.get("folder"):
        d = Path(fields["folder"]).expanduser().resolve()
        if not d.is_dir():
            raise ValueError(f"Folder not found: {fields['folder']}")
        fields["folder"] = str(d)
    lim = {"name": 80, "area": 40, "goal": 600, "finish_line": 300, "next_action": 200}
    clean = {k: (" ".join(str(v).split())[:lim[k]] if k in lim and k != "goal" else str(v)[:lim.get(k, 600)]) for k, v in fields.items()}
    if clean:
        sets = ", ".join(f"{k}=?" for k in clean)
        with _db() as c:
            try:
                c.execute(f"UPDATE projects SET {sets}, updated=? WHERE id=?", (*clean.values(), _now(), p["id"]))
            except sqlite3.IntegrityError:
                raise ValueError("That name is already used by another project.")
    return get(p["id"])


def delete(ref):
    p = get(ref)
    if p:
        with _db() as c:
            c.execute("DELETE FROM projects WHERE id=?", (p["id"],))
    return {"status": "deleted"} if p else {"error": "No such project."}


def milestones(pid):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM milestones WHERE project_id=? ORDER BY done, CASE WHEN due='' THEN 1 ELSE 0 END, due, id", (pid,))]


def add_milestone(pid, title, due=""):
    if not get(pid):
        raise ValueError("No such project.")
    with _db() as c:
        cur = c.execute("INSERT INTO milestones(project_id,title,due) VALUES(?,?,?)", (pid, " ".join(title.split())[:120], workflows.resolve_due(due) if due.strip() else ""))
        return cur.lastrowid


def toggle_milestone(mid, done=None):
    with _db() as c:
        r = c.execute("SELECT done FROM milestones WHERE id=?", (mid,)).fetchone()
        if not r:
            raise ValueError("No such milestone.")
        c.execute("UPDATE milestones SET done=? WHERE id=?", (int(not r["done"]) if done is None else int(done), mid))


def delete_milestone(mid):
    with _db() as c:
        c.execute("DELETE FROM milestones WHERE id=?", (mid,))


def git_info(folder):
    if not folder or not (Path(folder) / ".git").exists():
        return None
    def g(*a):
        return subprocess.run(["git", "-C", folder, *a], capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        last = g("log", "-1", "--format=%ct\t%s")
        ts, _, subj = last.partition("\t")
        return {"branch": g("rev-parse", "--abbrev-ref", "HEAD"), "dirty": len([l for l in g("status", "--porcelain").splitlines() if l.strip()]),
                "last_ts": int(ts) if ts.isdigit() else 0, "last_subject": subj[:90]}
    except Exception:
        return None


def stats(p):
    t_open = tasks.for_project(p["id"])
    t_all = tasks.for_project(p["id"], include_done=True)
    ms = milestones(p["id"])
    g = git_info(p["folder"])
    last = max([0] + ([g["last_ts"]] if g else []) + [dt.datetime.fromisoformat(t["done_at"]).timestamp() for t in t_all if t.get("done_at")] + [dt.datetime.fromisoformat(p["updated"]).timestamp()])
    days = (dt.datetime.now().timestamp() - last) / 86400
    return {"open_tasks": len(t_open), "done_tasks": len(t_all) - len(t_open), "milestones_total": len(ms), "milestones_done": sum(m["done"] for m in ms),
            "git": g, "stalled": p["status"] == "active" and days >= 14, "quiet_days": int(days)}


def list_(status=None):
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM projects" + (" WHERE status=?" if status else "") + " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'idea' THEN 1 WHEN 'paused' THEN 2 ELSE 3 END, updated DESC", (status,) if status else ())]
    for r in rows:
        r["stats"] = stats(r)
    return rows


def discover(base=None, max_depth=2):
    base = Path(base or Path.home())
    have = {p["folder"] for p in list_()} if DB.exists() else set()
    out = []

    def walk(d, depth):
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if not e.is_dir() or e.name.startswith(".") or e.name in SKIP or e.is_symlink():
                continue
            if (e / ".git").exists():
                g = git_info(str(e))
                out.append({"path": str(e), "name": e.name, "last_ts": g["last_ts"] if g else 0, "last_subject": g["last_subject"] if g else "", "imported": str(e) in have})
            elif depth < max_depth:
                walk(e, depth + 1)
    walk(base, 1)
    return sorted(out, key=lambda x: -x["last_ts"])


def import_paths(paths):
    made, skipped = [], []
    for path in paths:
        g = git_info(path)
        recent = bool(g and (dt.datetime.now().timestamp() - g["last_ts"]) < 45 * 86400)
        try:
            made.append(create(Path(path).name, goal="", folder=path, status="active" if recent else "paused")["name"])
        except ValueError as e:
            skipped.append(f"{Path(path).name}: {e}")
    return {"imported": made, "skipped": skipped}


def apply_plan(plan: dict):
    pid = plan["project_id"]
    p = get(pid)
    if not p:
        raise ValueError("Project no longer exists.")
    n_m = n_t = 0
    for m in plan.get("milestones", []):
        add_milestone(pid, m["title"], m.get("due", ""))
        n_m += 1
        for t in m.get("tasks", []):
            task = tasks.add(t["title"], workflows.resolve_due(t.get("due", "")) if t.get("due") else None)
            tasks.set_project(task["id"], pid)
            n_t += 1
    if plan.get("next_action") and not p["next_action"]:
        update(pid, next_action=plan["next_action"])
    return {"milestones": n_m, "tasks": n_t}


PLAN_SCHEMA = {"type": "object", "required": ["milestones", "next_action", "risks"], "properties": {
    "next_action": {"type": "string"}, "risks": {"type": "array", "items": {"type": "string"}},
    "milestones": {"type": "array", "items": {"type": "object", "required": ["title", "tasks"], "properties": {"title": {"type": "string"}, "due": {"type": "string"},
                   "tasks": {"type": "array", "items": {"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}, "due": {"type": "string"}}}}}}}}}


def make_plan(ref, guidance=""):
    # board #765 safety-plan-waits-approval
    import proposals
    p = get(ref)
    if not p:
        raise ValueError("No such project.")
    existing = [t["title"] for t in tasks.for_project(p["id"])][:12]
    ms = [m["title"] for m in milestones(p["id"])]
    g = git_info(p["folder"])
    today = workflows.today()
    import claude_plan
    r = claude_plan.llm_json(  # board #765 why-plan-model-choice
        "You plan a small project for a busy user: calm, concrete, honest. Make 3 to 5 milestones (each a visible result, not an activity), each with 1 to 4 small tasks that fit "
        "in under 90 minutes and start with a verb. 'next_action' = the single smallest first step to do next (under 12 words). risks = up to 3 honest risks or unknowns (including "
        "'this may be harder than it looks' if so). Use 'due' only as date words or YYYY-MM-DD, or leave it empty; never invent deadlines. Do not repeat existing tasks or milestones. Plain words.",
        f"Today is {today:%A %Y-%m-%d}.\nPROJECT: {p['name']}\nGOAL: {p['goal'] or '(not written yet)'}\nDONE WHEN: {p['finish_line'] or '(not written yet)'}\n"
        f"STATUS: {p['status']}\nGIT: {g if g else 'none'}\nEXISTING MILESTONES: {ms}\nEXISTING TASKS: {existing}\nUSER'S EXTRA GUIDANCE: {guidance or '(none)'}", PLAN_SCHEMA)
    clean = lambda s, n: " ".join(str(s).split())[:n]
    plan = {"project_id": p["id"], "next_action": clean(r["next_action"], 120),
            "milestones": [{"title": clean(m["title"], 100), "due": clean(m.get("due", ""), 30), "tasks": [{"title": clean(t["title"], 100), "due": clean(t.get("due", ""), 30)} for t in m["tasks"][:4] if t["title"].strip()]}
                           for m in r["milestones"][:5] if m["title"].strip()]}
    detail = "; ".join(f"{m['title']} ({len(m['tasks'])} tasks)" for m in plan["milestones"])
    pid = proposals.add("plan", f"Plan for {p['name']}", plan, detail=detail[:380], source=f"project:{p['id']}")
    return {"proposal_id": pid, "next_action": plan["next_action"], "milestones": [m["title"] for m in plan["milestones"]], "risks": [clean(x, 140) for x in r["risks"][:3]],
            "note": "Added to the user's Inbox for approval; nothing is created until they approve it."}


def next_actions(limit=6):
    out = []
    for p in list_("active"):
        if p["next_action"]:
            out.append({"project": p["name"], "next_action": p["next_action"], "stalled": p["stats"]["stalled"], "quiet_days": p["stats"]["quiet_days"]})
    return sorted(out, key=lambda x: -x["stalled"])[:limit]


def weekly_review() -> str:
    ps = list_()
    act = [p for p in ps if p["status"] == "active"]
    stalled = [p for p in act if p["stats"]["stalled"]]
    no_next = [p for p in act if not p["next_action"]]
    L = [f"{len(act)} active project{'s' if len(act) != 1 else ''}, {len(stalled)} stalled (quiet 14+ days), {len(no_next)} without a next action."]
    L += [f"Stalled: {p['name']} ({p['stats']['quiet_days']} days)" for p in stalled[:4]]
    L += [f"Needs a next action: {p['name']}" for p in no_next[:4]]
    return "\n".join(L)
