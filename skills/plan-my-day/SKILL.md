---
name: plan-my-day
description: "Use when the user asks what to do now or next, how the day looks, or wants to move things because the day changed."
---

1. Get facts first with what_now, get_schedule and list_tasks. Never guess.
2. Give ONE next step, then what comes after.
3. Classes and appointments never move, and you never create them. Only flexible work blocks and tasks move. If an appointment and a class clash, the appointment wins: tell the user and move neither.
4. Running late: shift_day(minutes_late). It skips classes and appointments itself and asks the user first when many events move or something would overlap.
5. One block to move: move_event with the real id from get_schedule. New work time: find_free_time, then propose_event.
6. Full morning routine: start_my_day. Evening: wrap_up_day.
