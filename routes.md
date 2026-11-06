# Rosie SPA route map

Hash routes for `http://127.0.0.1:8765`. Several sidebar names do not match their route key.

| Sidebar name | Route | Notes |
|---|---|---|
| Home | `#/` | |
| Inbox | `#/inbox` | |
| Calendar | `#/calendar` | |
| Tasks | `#/tasks` | |
| Mail | `#/mail` | |
| Life Quest | `#/lifequest` | |
| **Morning** | **`#/paper`** | Named "paper" internally |
| Projects | `#/projects` | |
| Meetings | `#/meetings` | |
| Notes | `#/notes` | |
| **Agent Zero** | **`#/agentzero`** | |
| Rosie's corner | `#/corner` | |
| Memory | `#/memory` | |
| Skills | `#/skills` | |
| Vector | `#/vector` | |
| **Agent Deck** | **`#/deck`** | Shows Agent Deck (in the System menu group) |
| Settings | `#/settings` | |

## Navigating via DevTools (Wayland workaround)

When coordinate clicks are blocked by the Wayland remote desktop portal (multi-monitor setups):

1. Open Chrome DevTools: `Ctrl+Shift+P` → type "Show Console" → Enter.
2. In the Console: `location.hash = '#/route'` + Enter.
3. Take screenshot with `screenshot window_id=<rosie-window-id>`.
