"""Skills = plain-text playbooks in vault/skills/*.md. First line = when to use it. The assistant sees the list
every turn and loads the matching one itself. Edit or add files freely."""
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "vault" / "skills"

DEFAULTS = {
"test-an-idea": ("Use when the user shares a business, product or project idea and wants feedback.",
"""1. Restate the idea in one line.
2. Name the BIGGEST risk or flaw first. Be honest, do not flatter.
3. Say what is genuinely good about it.
4. Give the smallest, cheapest test they could run this week.
5. If it depends on facts (prices, rules, competitors, feasibility), use web_search and say what you found.
6. Offer to save it with remember(kind=idea) or turn it into a project."""),
"meal-planning": ("Use for food, meals, groceries, cooking, diet, restaurants, takeout or what to eat.",
"""1. recall(kind=food) and recall(kind=profile) FIRST and follow every saved rule for everyone the meal is for. If the exact restrictions, allergies or doctor advice are unknown, ask ONE question and then remember() the answer.
2. Be honest: you are not a doctor. Evidence on special diets is often mixed; say 'research suggests', never 'this cures'.
3. Medication or supplement doses and timing go to their doctor or pharmacist. You may mention that food timing can matter and tell them to confirm.
4. Restaurants: use phone_search or web_search to find places, then READ the menu or allergy page (fetch_url). Give 2 or 3 options and ONE top pick that fits the saved diet rules. Never say a dish is safe: say what the menu states and that they should ask staff.
5. Keep it simple: few ingredients, one-pan or batch cooking, a grocery list at the end.
6. Save lasting preferences with remember(kind=food)."""),
"save-recipe": ("Use when the user wants a recipe saved to a Keep note (or a note) from the web.",
"""1. recall(kind=food) first so the recipe fits their saved diet rules. 
2. find_recipe to read a REAL page (never write a recipe from memory). Pick the best-rated one that fits the diet.
3. phone_keep_note ONCE with a clear title, the ingredients, the steps, and the source link. It only creates a new note; it cannot edit or delete one.
4. Say plainly which recipe you picked and why, and what you changed or checked for the diet. Do not say it is saved until the tool succeeded."""),
"reply-to-text": ("Use when the user wants to reply to or answer a text, message or notification.",
"""1. phone_notifications (or phone_read_messages) to find the message being answered.
2. If the user gave the words, send exactly those. Otherwise write one short reply in their voice from what they said.
3. Call phone_reply_notification (or phone_send_sms with the contact name) right away: the tool asks the user to confirm. Never invent a number or send without that.
4. Reply only with what is about to be sent. Do not claim it was sent until it was."""),
"build-something": ("Use when the user wants to build, make, code, set up or fix something (software, hardware, business, a project).",
"""1. Check feasibility first. Say plainly what is hard or impossible with their hardware and tools. Use web_search if unsure.
2. Break it into small steps; do ONE step at a time and test it.
3. Use the PC tools to actually create files, run code and test, then report what really happened (including failures).
4. Save project state with remember(kind=project) so the next session can continue."""),
"plan-my-day": ("Use when the user asks what to do now/next, how the day looks, or needs to reschedule.",
"""1. Use what_now / get_schedule / list_tasks for facts; never guess.
2. Give ONE next step, then what comes after.
3. If they are running late, use shift_day (deletes and big changes get a Confirm card automatically).
4. For the full morning routine use start_my_day; for the evening use wrap_up_day."""),
"project-planning": ("Use when the user wants to plan, scope, break down or review a project, set a goal or finish line, or decide the next step on a project.",
"""1. Use list_projects / project_status to see what exists. If it is a new project, call create_project RIGHT AWAY with a clear one-sentence goal and a 'done when' finish line taken from what they said. The folder is optional: never ask about it. Only ask (ONE question) if you truly cannot tell what the project is.
2. Be honest about feasibility: say what is hard, uncertain, or likely to take longer than they hope. Use web_search if a fact decides it.
3. Keep ONE next action per project: the single smallest step, under 12 words. Save it with update_project(field=next_action).
4. To break a project down, call plan_project (optionally with the user's constraints as guidance). It parks the plan in their Inbox for approval; tell them to approve it there.
5. Time blocks are only suggestions: propose_event, never silently add. Don't pile on: 3 active projects at a time is plenty for most people; say so if they have more."""),
"life-support": ("Use for decisions, habits, money, stress, motivation, relationships or feeling overwhelmed.",
"""1. Be warm but straight. If they seem overwhelmed, pick the single most important next step and offer only that.
2. For medical, legal or financial specifics give general information, say where it stops, and suggest a professional when it matters.
3. Save lasting facts or preferences with remember(kind=profile)."""),
}


def init():
    import testguard
    testguard.check(DIR)
    DIR.mkdir(parents=True, exist_ok=True)
    for name, (desc, body) in DEFAULTS.items():
        p = DIR / f"{name}.md"
        fresh = not p.exists()
        if fresh:
            p.write_text(f"{desc}\n\n{body}\n")


def listing() -> list[tuple[str, str]]:
    init()
    out = []
    for p in sorted(DIR.glob("*.md")):
        first = p.read_text().split("\n", 1)[0].strip()
        out.append((p.stem, first))
    return out


def use_skill(name: str):
    init()
    p = DIR / f"{Path(name).stem}.md"
    if not p.exists():
        return {"error": f"No skill named '{name}'.", "available": [n for n, _ in listing()]}
    return {"skill": p.stem, "instructions": p.read_text()}
