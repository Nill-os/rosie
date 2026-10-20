# Rosie

Rosie is a private AI assistant that runs on your own Linux PC. The AI model, speech-to-text, and text-to-speech all run locally, so your chats, notes, and calendar never leave the computer. Agent Zero is her brain for bigger tasks, and it uses her tools: your calendar, tasks, reminders, mail, notes, projects, phone, and PC.

## What she does

- **Chat, voice, and a phone app.** Type in her window, hold a key to talk, or use the Android app over Tailscale.
- **Your day.** Tasks, reminders, Google Calendar, a daily brief, and a weekly review.
- **Mail.** Reads your Gmail, flags what matters, and drafts replies. She never deletes anything.
- **Notes and memory.** A searchable notes vault, things she remembers about you, and meeting transcripts.
- **The Morning Paper.** A printed and on-screen newspaper made each morning from real news feeds, Hacker News, Reddit, your calendar, your deliveries, the weather, a cartoon, words to learn, a cipher, and a Sudoku. Nothing in it is invented.
- **Rosie's corner.** When you aren't using her, she browses public pages about your interests and keeps what she finds.
- **Your PC and phone.** Runs commands, opens files and apps, sends texts, and sets alarms. Anything risky waits for your Yes on a confirm card.

## What you need

- Ubuntu or Debian Linux with a desktop.
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

## Everyday use

- **Start:** click the desktop icon. It starts anything that isn't running, then opens her window.
- **Shut down all:** use the button at the bottom of the sidebar, or right-click the desktop icon. It stops Agent Zero, the memory graph, push-to-talk, the overlay, and the AI models, then Rosie. Nothing is lost.
- **Agent Zero page:** shows whether Agent Zero is running, what it's working on, which of Rosie's tools it used, and its recent activity. You can restart or stop it there.
- **Settings:**
  - **Brain:** choose who answers chat, Agent Zero or Rosie's own model.
  - **Context window:** **Auto-tune** finds the biggest window that keeps the model fully on your graphics card.
  - **Newspaper:** set its name, place, time, sections, feeds, puzzles, printer, and email.

## Optional extras

- **Google Calendar and Gmail:** run `./login.sh` and `./login-gmail.sh main`. You need your own Google Cloud OAuth client in `secrets/google_client.json`.
- **The phone app and access away from home:** install Tailscale, then run `sudo tailscale up` and `sudo tailscale serve --bg 8765`. Build the app with `android/build.sh`.
- **Reddit in the newspaper:** create a free Reddit "script" app, then save its keys in `secrets/reddit.json` as `{"client_id": "...", "client_secret": "...", "user": "your_reddit_name"}`.
- **Your town in the newspaper:** add your local news site's RSS feed in **Settings** > **Newspaper**. Event calendars differ in every town, so Rosie doesn't ship an events reader, and the paper leaves out its "week ahead" when there are no events.
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
