from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "vault" / "skills"

DEFAULTS = {  # board #776 owner-audit-merged-save-recipe
"test-an-idea": ("Use when the user shares a business, product or project idea and wants feedback.",
"""1. Restate the idea in one line.
2. Name the BIGGEST risk or flaw first. Be honest, do not flatter.
3. Say what is genuinely good about it.
4. Give the smallest, cheapest test they could run this week.
5. If it depends on facts (prices, rules, competitors, feasibility), use web_search and say what you found.
6. Offer to save it with remember(kind=idea), or to make it a project with create_project."""),
"meal-planning": ("Use for food, meals, recipes, groceries, cooking, diet, restaurants, takeout or what to eat, including saving a recipe to a note.",
"""1. recall(kind=food) and recall(kind=profile) FIRST and follow every saved rule for everyone the meal is for. If the exact restrictions, allergies or doctor advice are unknown, ask ONE question and then remember(kind=food) the answer.
2. Be honest: you are not a doctor. Evidence on special diets is often mixed; say 'research suggests', never 'this cures'.
3. Medication or supplement doses and timing go to their doctor or pharmacist. You may mention that food timing can matter and tell them to confirm.
4. Restaurants: use web_search to find places, then READ the menu or allergy page (fetch_url). Give 2 or 3 options and ONE top pick that fits the saved diet rules. Never say a dish is safe: say what the menu states and that they should ask staff.
5. Recipes: call find_recipe (it reads real recipe pages); never write amounts or steps from memory. If its pick breaks a diet rule, call it again with the rule in the query (for example 'gluten-free chili').
6. To save a recipe: phone_keep_note ONCE with the title, ingredients, steps and source link. It only creates a new note. Say it is saved only after the tool succeeded, and say what you checked for the diet.
7. Keep it simple: few ingredients, one-pan or batch cooking, a grocery list at the end.
8. Save lasting preferences with remember(kind=food)."""),
"reply-to-text": ("Use when the user wants to reply to or answer a text, message or notification.",
"""1. phone_notifications (or phone_read_messages) to find the message being answered.
2. If the user gave the words, send exactly those. Otherwise write one short reply in their voice from what they said.
3. If the notification can be replied to, call phone_reply_notification with its id and the text. For an SMS, call phone_send_sms with the contact name or the number from the message. For another app without a reply box, use phone_screen_do. An email: draft_email_reply (it only drafts). Every send asks the user to confirm first; never invent a number.
4. Reply only with what is about to be sent. Do not claim it was sent until it was."""),
"build-something": ("Use when the user wants hands-on help making, setting up or fixing a concrete thing now: code, a device, a setup. Not for planning (project-planning) or judging an idea (test-an-idea).",
"""1. Check feasibility first. Say plainly what is hard or impossible with their hardware and tools. Use web_search if unsure.
2. Break it into small steps; do ONE step at a time and test it.
3. Do the work with run_command, read_file, write_file and list_dir (terminal_start for long jobs). Report what really happened, including failures.
4. If it belongs to a project (list_projects), save the next step with update_project(field=next_action). Otherwise remember(kind=project) a one-line state note."""),
"plan-my-day": ("Use when the user asks what to do now or next, how the day looks, or wants to move things because the day changed.",
"""1. Get facts first with what_now, get_schedule and list_tasks. Never guess.
2. Give ONE next step, then what comes after.
3. Classes and appointments never move, and you never create them. Only flexible work blocks and tasks move. If an appointment and a class clash, the appointment wins: tell the user and move neither.
4. Running late: shift_day(minutes_late). It skips classes and appointments itself and asks the user first when many events move or something would overlap.
5. One block to move: move_event with the real id from get_schedule. New work time: find_free_time, then propose_event.
6. Full morning routine: start_my_day. Evening: wrap_up_day."""),
"project-planning": ("Use when the user wants to plan, scope, break down or review a project, set a goal or finish line, or decide the next step on a project.",
"""1. Use list_projects / project_status to see what exists. If it is a new project, call create_project RIGHT AWAY with a clear one-sentence goal and a 'done when' finish line taken from what they said. The folder is optional: never ask about it. Only ask (ONE question) if you truly cannot tell what the project is.
2. Be honest about feasibility: say what is hard, uncertain, or likely to take longer than they hope. Use web_search if a fact decides it.
3. Keep ONE next action per project: the single smallest step, under 12 words. Save it with update_project(field=next_action).
4. To break a project down, call plan_project (optionally with the user's constraints as guidance). It parks the plan in their Inbox for approval; tell them to approve it there.
5. Time blocks are only suggestions: find_free_time, then propose_event. Add one directly (add_event) only when the user names the exact time. Don't pile on: 3 active projects at a time is plenty for most people; say so if they have more."""),
"life-support": ("Use for decisions, habits, money, stress, motivation, relationships or feeling overwhelmed.",
"""1. Be warm but straight. If they seem overwhelmed, pick the single most important next step and offer only that.
2. For medical, legal or financial specifics give general information, say where it stops, and suggest a professional when it matters.
3. Save only a lasting preference that changes how you help (one short sentence) with remember(kind=profile). Never save debts, account numbers, health or legal details (diet rules for meals go through meal-planning as kind=food)."""),
}


# board #776 why-old-defaults-replaced
OLD_DEFAULTS = {"test-an-idea": {"d1e28afc721c8003"},
                "meal-planning": {"09886bc1a32932bf", "2e5f1675b47ae247", "6fbc46a234cf3155", "f7b42b99cdd46e9d"},
                "save-recipe": {"7d2a1306741f91ce", "80ca73eb70a316fe", "39501aca6cb3e848"}, "reply-to-text": {"46c3687ec1c230e7"},
                "build-something": {"234909fd1bc1ed0b"}, "plan-my-day": {"cedf71ed1aa08179"}, "project-planning": {"b5dffeb513f33919"},
                "life-support": {"e4eff804806b4a66"}}
RETIRED = {"save-recipe"}


def _unedited(p: Path, name: str) -> bool:
    import hashlib
    try:
        return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16] in OLD_DEFAULTS.get(name, ())
    except OSError:
        return False


def init():
    import testguard
    testguard.check(DIR)
    DIR.mkdir(parents=True, exist_ok=True)
    for name in RETIRED:
        if _unedited(DIR / f"{name}.md", name):
            (DIR / f"{name}.md").unlink()
    for name, (desc, body) in DEFAULTS.items():
        p = DIR / f"{name}.md"
        fresh = not p.exists() or _unedited(p, name)
        if fresh:
            p.write_text(f"{desc}\n\n{body}\n", encoding="utf-8", newline="\n")


def listing() -> list[tuple[str, str]]:
    init()
    out = []
    for p in sorted(DIR.glob("*.md")):
        try:
            first = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0].strip()
        except OSError:
            continue
        out.append((p.stem, first))
    return out


def copied(name: str) -> bool:
    p = DIR / f"{Path(name).stem}.md"
    return p.exists() and "\nMirrored-from: " in "\n" + "\n".join(p.read_text(errors="replace", encoding="utf-8").split("\n")[:8])


def prompt_line() -> str:
    own, other = [], []
    for n, d in listing():
        (other if copied(n) else own).append((n, d))
    line = "; ".join(f"{k}: {d.rstrip('.')}" for k, d in own)
    if other:
        line += (". Also skills shared with Claude and Agent Zero (use_skill loads one; its first line says when to use it): "
                 + ", ".join(n for n, _ in other))
    return line


def use_skill(name: str):
    init()
    p = DIR / f"{Path(name).stem}.md"
    if not p.exists():
        return {"error": f"No skill named '{name}'.", "available": [n for n, _ in listing()]}
    return {"skill": p.stem, "instructions": p.read_text()}
