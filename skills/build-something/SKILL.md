---
name: build-something
description: "Use when the user wants hands-on help making, setting up or fixing a concrete thing now: code, a device, a setup. Not for planning (project-planning) or judging an idea (test-an-idea)."
---

1. Check feasibility first. Say plainly what is hard or impossible with their hardware and tools. Use web_search if unsure.
2. Break it into small steps; do ONE step at a time and test it.
3. Do the work with run_command, read_file, write_file and list_dir (terminal_start for long jobs). Report what really happened, including failures.
4. If it belongs to a project (list_projects), save the next step with update_project(field=next_action). Otherwise remember(kind=project) a one-line state note.
