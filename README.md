# Rosie

Rosie is a private AI assistant that runs on your own PC, Linux or Windows. The AI model, speech-to-text, and text-to-speech all run locally, so your chats, notes, and calendar never leave the computer. Agent Zero is her brain for bigger tasks, and it uses her tools: your calendar, tasks, reminders, mail, notes, projects, phone, and PC.

## What she does

- **Chat, voice, and a phone app.** Type in her window, hold a key to talk, or use the Android app over Tailscale.
- **Your day.** Tasks, reminders, Google Calendar, a daily brief, and a weekly review.
- **Mail.** Reads your Gmail, flags what matters, and drafts replies. She never deletes anything.
- **Notes and memory.** A searchable notes vault, meeting transcripts, and her own long-term memory: while you aren't using her, she reads your chats into facts and beliefs that grow as she hears more, and brings the ones that matter into each conversation. Ask her what she remembers, or tell her to forget something.
- **The Morning Paper.** A printed and on-screen newspaper made each morning from real news feeds, Hacker News, Reddit, your calendar, your deliveries, the weather, a cartoon, words to learn, a cipher, and a Sudoku. Nothing in it is invented.
- **Rosie's corner.** When you aren't using her, she browses public pages about your interests and keeps what she finds. What she read comes into a chat when it matches what you ask.
- **Your PC and phone.** Runs commands, opens files and apps, sends texts, and sets alarms. Anything risky waits for your Yes on a confirm card.
- **Tasks.** Tap a task to see its details, notes, and the email it came from.
- **Mail organizer.** Sorts Gmail into your own labels, turns meeting requests in mail into calendar events, lists action items on the Mail page, and keeps one undo.
- **Study page.** Flash cards with spaced review, walk mode, cards made from your notes, printing, practice tests, a tutor, study ideas sent from the phone, and the next study block on your calendar.
- **Life Quest page.** Stats, quests, XP that needs evidence, and a weekly review.
- **Agent Deck page.** A live view of every agent and process (CPU, RAM, and GPU at a glance), with controls to stop or restart them. Works on Windows too.
- **Memory graph page.** A map of her memories (her corner's finds, the agent board's history, or what she remembers about you), with a Clusters view; Ctrl+click several things to see how they connect.
- **Servers page.** A live list of the machines and services on your home network, each one click to open.
- **Security watch.** A nightly check of her packages, the PC, and its containers against public lists of known and exploited security holes, with patching for the parts she manages.
- **Model scout.** Twice a day she looks for newer local models, tests one at night on her own tasks, and tells you in the Inbox when one does better. She never switches by herself.
- **Agent board.** Rosie, Agent Zero, and your Claude Code sessions see what the others are doing, leave each other notes, and claim shared work so they don't collide. Two PCs can share one board.
- **Shared skills.** Every 10 minutes Claude Code, Rosie, and Agent Zero copy each other's skills, so all three have the same set.
- **Claude as a checker (optional).** If Claude Code is installed, Agent Zero plans bigger tasks with Claude and Claude checks its answers before you see them. When Claude can't be used, Rosie's own checks stand in.
- **Vector robot (optional).** Rosie can be the brain of an Anki/DDL Vector on WireOS: her voice through his speaker, "Hey Rosie", clock and PC screens while he sits, a settings menu on his face, drive him with a PS4, PS5, or Xbox controller paired with the PC, and "look around" or "find" orders. Turn it off in **Settings** > **Menu** if you don't have one.
- **Looks.** An easy-to-read Jetsons theme besides the neon one, menu groups that fold, an icon rail at half-screen width, and one Display setting for zoom and text size.

## What you need

- Ubuntu or Debian Linux with a desktop, or 64-bit Windows (see [Install on Windows](#install-on-windows)).
- An NVIDIA graphics card with 8 GB or more is recommended. Without one, the model runs on the CPU and replies are slow.
- 16 GB of RAM and about 30 GB of free disk space.
- Chrome or Chromium.

## Install

1. Clone Rosie into `~/assistant`. The path matters.

   ```bash
   git clone https://github.com/Nill-os/rosie ~/assistant
   ```

2. Run the setup script and follow its prompts:

   ```bash
   cd ~/assistant && ./setup.sh
   ```

   It installs the system packages, Ollama and the model, Docker containers for Agent Zero and Rosie's memory graph, her service, the desktop icon, and push-to-talk. It also measures the biggest context window your graphics card can hold. It asks for your password when it needs admin rights. If it adds you to the `docker` group, log out and back in, then run it again.

3. Open **Rosie** from the desktop icon and sign in with the login code the script shows. It's also saved in `~/assistant/secrets/access_code`.

To use a different model, run `ROSIE_MODEL=<name> ./setup.sh`, or pick one later in **Settings** > **AI model**.

## Install on Windows

1. Open PowerShell and run:

   ```powershell
   irm https://raw.githubusercontent.com/Nill-os/rosie/main/tools/windows/install.ps1 | iex
   ```

   It checks what the PC already has and adds only what is missing: Ollama (from winget), Python, Rosie, and the model. Agent Zero needs Docker Desktop. Rosie goes in `%LOCALAPPDATA%\Rosie`. Run it again any time to repair an install. It keeps Rosie's data.

2. Wait for Rosie to open in your browser, and sign in with the access code shown in the Rosie window. She starts by herself each time you sign in to Windows.

Windows copies update themselves from this repo. They take a new version only after every GitHub check on it passed, test it first, and keep the old version if the new one doesn't start.

## Everyday use

- **Start:** click the desktop icon. It starts anything that isn't running, then opens her window.
- **Shut down all:** use the button at the bottom of the sidebar, or right-click the desktop icon. It stops Agent Zero, the memory graph, push-to-talk, the overlay, and the AI models, then Rosie. Nothing is lost.
- **Agent Zero page:** shows whether Agent Zero is running, what it's working on, which of Rosie's tools it used, and its recent activity. You can restart or stop it there.
- **Settings:**
  - **Brain:** choose who answers chat, Agent Zero or Rosie's own model.
  - **Context window:** **Auto-tune** finds the biggest window that keeps the model fully on your graphics card.
  - **Newspaper:** set its name, place, time, sections, feeds, puzzles, printer, and email.

## Optional extras

- **Google Calendar and Gmail:** run `./login.sh` and `./login-gmail.sh main` (on Windows, open **Rosie Google Sign-in** from your desktop). You need your own Google Cloud OAuth client in `secrets/google_client.json`.
- **The phone app and access away from home:** install Tailscale, then run `sudo tailscale up` and `sudo tailscale serve --bg 8765`. Build the app with `android/build.sh`.
- **Reddit in the newspaper:** create a free Reddit "script" app, then save its keys in `secrets/reddit.json` as `{"client_id": "...", "client_secret": "...", "user": "your_reddit_name"}`.
- **Your town in the newspaper:** add your local news site's RSS feed in **Settings** > **Newspaper**. Event calendars differ in every town, so Rosie doesn't ship an events reader, and the paper leaves out its "week ahead" when there are no events.
- **Rosie's memory in Claude Code:** run `claude mcp add --scope user rosie-mind -- ~/assistant/.venv/bin/python ~/assistant/app/mind_mcp.py`. Claude can then search what Rosie remembers and tell her things worth remembering.
- **Skills:** ask her to find a skill for a task. She searches the free skills.sh index, and adds one as a playbook only after you tap Yes (text only: she never installs programs or anything that needs a paid service).
- **Encrypted backups to GitHub:** put the name of a private repo, for example `you/rosie-data`, in `secrets/backup_repo`, and run `./backup-data.sh` from a daily timer. Without it, backups stay in `~/backups/rosie`.

## How it fits together

| Part | Where it runs | Notes |
|---|---|---|
| Rosie (web app, tools, schedules) | `assistant.service`, port 8765 | Listens on this PC only. Tailscale is optional. |
| Agent Zero (the brain) | Docker container `agent-zero`, port 50080 | Reaches Rosie's tools over MCP on the Docker bridge, with a token. |
| AI model | Ollama, port 11434 | Every caller uses the same context window, so the model never reloads. |
| Memory graph | Docker container `rosie-graph` (FalkorDB) | Holds her corner's finds. |
| Push-to-talk and overlay | User services `assistant-dictation`, `assistant-overlay` | X11. |
| Updates | `~/assistant` and `~/assistant-b` (A/B code slots), `~/rosie-ops` | Each update is tested before switching, and it switches back by itself if the new version stops answering. |

## Privacy and safety

- Everything runs on your PC. The only network calls are the ones you turn on: news feeds, web search, Google, Reddit, and the phone link.
- Text from web pages, mail, files, and the phone is treated as data, never as instructions.
- Texts, calls, deleting or moving events, mail cleanup, PC commands, and changes to her own code wait for your Yes on a confirm card.
- Agent Zero stops a task by itself after 50 steps, so a confused model can't loop forever.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The page tests need jsdom. Run `npm install --no-save jsdom` in `~/assistant` first, or they're skipped.

## License

Rosie is under the MIT License (see `LICENSE`). Third-party word lists and fonts keep their own licenses, which sit next to the files.
