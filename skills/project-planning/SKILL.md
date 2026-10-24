---
name: project-planning
description: "Use when the user wants to plan, scope, break down or review a project, set a goal or finish line, or decide the next step on a project."
---

1. Use list_projects / project_status to see what exists. If it is a new project, call create_project RIGHT AWAY with a clear one-sentence goal and a 'done when' finish line taken from what they said. The folder is optional: never ask about it. Only ask (ONE question) if you truly cannot tell what the project is.
2. Be honest about feasibility: say what is hard, uncertain, or likely to take longer than they hope. Use web_search if a fact decides it.
3. Keep ONE next action per project: the single smallest step, under 12 words. Save it with update_project(field=next_action).
4. To break a project down, call plan_project (optionally with the user's constraints as guidance). It parks the plan in their Inbox for approval; tell them to approve it there.
5. Time blocks are only suggestions: find_free_time, then propose_event. Add one directly (add_event) only when the user names the exact time. Don't pile on: 3 active projects at a time is plenty for most people; say so if they have more.
