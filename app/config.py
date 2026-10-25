import json
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"
DEFAULTS = {
    "chat_model": "qwen3.5:9b",      # the local Ollama model Rosie thinks with (Settings > AI model); see models.py
    "token_report_on": True,         # an hourly Claude token report (tokens.py) and one Inbox item a day while a flag is active
    "token_goal_main_share": 50,     # the main (top) model may write at most this % of Claude's output tokens over 7 days
    "token_goal_cache_read_ratio": 80,   # at least this % of Claude's input should come from the cache over 7 days
    "token_spike_factor": 2.0,       # today's output above this many times the daily average is flagged
    "model_scout": True,             # Rosie looks for new abliterated models, tests the best one at night and flags a better one (model_scout.py)
    "security_watch": True,          # a nightly check for known and exploited security holes in Rosie, this PC and the home server (security_watch.py)
    "security_autopatch": True,      # ...and she installs the safe fixes herself: her own packages (tested) and the containers' security updates
    "proxmox_ssh": "",               # the Proxmox host the security watch checks, e.g. root@192.168.1.10 (key login); empty = this PC only
    "proxmox_ssh_key": "",           # the SSH key for it, e.g. ~/.ssh/id_ed25519_proxmox
    "owner_name": "",               # what Rosie calls you in her own prompts (her corner); empty = "your owner"
    "study_calendar": "Study Plan",  # the Google calendar whose next event the Study page plans for (read only)
    "ui_theme": "aurora",           # the look for devices that have not picked their own (Settings > Display > Theme): aurora, cyberdeck or jetsons
    "menu_layout": [],              # the menu's groups and order (Settings > Menu): [{"label": "My day", "pages": ["home", ...]}]; empty = the built-in order
    "hidden_pages": [],             # pages left out of the menu (Settings > Menu), like ["vector"]
    "owner_notes": "",              # how you like help, added to her and Agent Zero's prompts (Settings > About you)
    "board_link_repo": "",          # "owner/repo": share the agent board with another PC through issue board_link_issue (board_link.py)
    "board_link_issue": 1,
    "board_link_pc": "",            # this PC's short name on the shared board (default: its host name)
    "board_link_dir": "",           # a folder both PCs share (Syncthing): the board goes through it instead of GitHub (board_link.py)
    "claude_planning": False,        # planning requests go to Claude (your Claude Code login), then Rosie carries out the plan (Settings > Planning; claude_plan.py). Off unless you turn it on: it sends your week to Anthropic
    "brain": "rosie",                # who answers chat: "rosie" = her own tool loop, "agent_zero" = Agent Zero with her tools (Settings > Brain; a0.py)
    "a0_chat_ui": True,              # Chat shows Agent Zero's own page when the brain is Agent Zero (a0_proxy.py; Settings > Agent Zero)
    "a0_proxy_port": 8771,           # the port that page is served on, behind Rosie's login
    "write_target": "test",          # "test" = Assistant Test calendar, "primary" = your real calendar
    "test_calendar_id": None,
    "max_changes_without_confirm": 3,
    "brief_on": True,
    "brief_time": "06:30",
    "remind_events": True,
    "remind_lead_min": 15,
    "nudge_overdue": True,
    "nudge_overdue_time": "10:00",
    "scratch_mode": "nudge",        # off | nudge | auto  (auto = sort the scratch pad for you)
    "scratch_min_lines": 3,
    "scratch_idle_min": 30,
    "wrapup_mode": "nudge",         # off | nudge | auto  (auto = run the end-of-day routine for you)
    "wrapup_time": "20:30",
    "gmail_accounts": [],            # [{"name": "main", "email": "you@gmail.com"}] (read-only access)
    "mail_on": True,
    "mail_check_min": 10,
    "mail_flag": True,              # pop-up + Inbox item for important mail
    "mail_find_tasks": True,        # propose tasks/events found in mail (you approve)
    "mail_digest": True,            # email section in the daily brief
    "canvas_time": "07:00",          # the daily Canvas due-date sync (Settings > Canvas; off until a feed link is saved)
    "mail_organize": False,         # sort mail into your own Gmail labels (you approve each batch on the Mail page)
    "mail_organize_archive": False, # also take labeled mail out of the Inbox (off: labels only)
    "mail_labels": {},              # {Gmail label name: short hint for the model}: the only labels Rosie may use; empty until you pick
    "agents": {                      # command templates; {prompt} is replaced by the (shell-quoted) task. Edit to add agents.
        "claude": "claude {prompt}",
        "codex": "codex {prompt}",
        "gemini": "gemini -i {prompt}",
        "opencode": "opencode run {prompt}",
    },
    "agents_notify": True,           # pop-up when an agent session finishes or is waiting for your answer
    "weekly_review_on": True,        # Sunday project review in the Inbox
    "weekly_review_time": "17:00",
    "meeting_mic": "default",        # PipeWire node name of your microphone, or "default"
    "meeting_call": "default",       # node name of the output (speaker/headset) whose sound is the call, or "default"
    "meeting_keep_audio": False,     # delete the audio once the transcript exists (recommended)
    "meeting_max_minutes": 180,      # hard stop so a forgotten recording cannot run all day
    "meeting_remind_min": 10,        # "still recording" reminder interval
    "glossary": "",                  # names/terms to help speech recognition, comma separated
    "dictation_on": True,
    "dictation_hotkey": "Control_R",  # hold alone to talk; release to type. Any other key pressed while held cancels (so Ctrl+C etc. still work)
    "dictation_min_hold_ms": 300,
    "dictation_max_seconds": 90,
    "dictation_cleanup": True,        # remove um/uh and stutters (plain rules, never the AI)
    "dictation_source": "default",    # microphone node name or "default"
    "dictation_notify": True,
    "sms_confirm": "ask",             # texts by voice: ask = read back and wait for send/change/cancel | headphones = auto-send (3 s cancel window) when headphones are on | always = auto-send
    "call_confirm": "announce",       # calls: ask = confirmation first | announce = say who, dial after a 3 s cancel window | now = dial right away
    "ai_on": True,                    # hold-to-talk to the AI from anywhere
    "ai_hotkey": "Alt_R",             # hold, speak, release: the AI answers out loud (a different key from dictation)
    "ai_orb_on": True,                # the small round button you can press and hold instead of a key
    "ai_orb_corner": "bottom-right",
    "ai_overlay_mode": "full",        # full | subtle | reduced (no flicker, for comfort) | off
    "ai_sink": "default",             # speaker node name or "default"
    "ai_speak": True,                 # say the answer out loud
    "ai_use_selection": True,         # include text you have highlighted when you ask (read as data, never as instructions)
    "vector_on": False,               # Rosie is the brain of a Vector robot on WireOS (Settings > Vector; vector_pod.py). Off until you set him up
    "vector_host": "",                # the address Vector uses to reach this PC; empty = this PC's home-network address
    "vector_port": 8084,              # Vector's voice, settings and token requests (gRPC over TLS)
    "vector_check_port": 8085,        # his "am I online" check (http and https)
    "vector_personality": "",         # how Vector talks when Rosie answers through him (added to her prompt for his turns)
    "vector_commands": [],            # your own voice commands: [{"phrases": ["sit"], "action": "hold|release|say", "text": "", "reply": "Okay."}]
    "vector_face_panels": ["clock", "system"],   # what his face shows while he sits, in turn (vector_face.PANELS)
    "vector_face_seconds": 15,        # each screen on his face while he sits shows this long
    "vector_face_size": "184x96",     # his screen in pixels (Vector 1.0 and 2.0 SDK size)
    "vector_quiet_idle": False,       # no idle chirps: he is muted except from his wake word until he has answered (and when Rosie speaks through him)
    "vector_talk_volume": "MEDIUM",   # his volume while he talks: LOW, MEDIUM_LOW, MEDIUM, MEDIUM_HIGH or HIGH
    "vector_eyes": "rosie",           # WireOS: "rosie" = his eyes sweep cyan to purple (her theme); "own" = his own eye colour
    "vector_stt_model": "base.en",    # speech to text for Vector: base.en is quick (about 0.5 s), small.en hears names better (about 1.5 s)
    "vector_followup": True,          # after he answers, he listens again for a follow-up (no wake word); silence ends the talk
    "vector_voice": "rosie",          # "rosie" = her own voice streamed to his speaker; "vector" = his built-in voice
    "vector_backpack": "own",         # WireOS: "rosie" = his backpack light patterns in cyan and purple (vector_pod.set_backpack_theme)
    "vector_life": "some",            # he speaks up on his own (picked up, petted, greeting you): off, some or lots (vector_life.py)
    "vector_pad": True,               # a game controller paired with this PC drives him (vector_pad.py; Linux)
    "vector_pad_speed": "normal",     # his top driving speed with the controller: slow, normal or fast
    "vector_wake_word": "hey vector",   # WireOS only: "hey rosie" puts Rosie's keyword file on him (vector_pod.set_wake_word)
    "vector_escapepod": False,        # his firmware is DDL's escape pod build: Rosie answers as escapepod.local:443 (set by Connect)
    "vector_robots": [],              # [{"esn": "00808493", "ip": "192.168.1.15"}]: only these robots may use the server
    "taint_guard": True,            # after reading untrusted text (web, email, files), risky tools need your confirmation
    "desktop_notify": True,
    "quiet_from": "22:00",
    "quiet_to": "06:30",
    "spaces": ["Work", "Projects", "Food", "Life", "Build", "Ideas"],
    "fixed_keywords": ["appointment", "dentist", "doctor", "root canal", "bank", "flight", "meeting with"],
}


def load():
    cfg = dict(DEFAULTS)
    if PATH.exists():
        try:
            cfg.update(json.loads(PATH.read_text()))
        except ValueError:                                         # a half-written file must not take the whole app down
            pass
    return cfg


_lock = threading.Lock()                                           # saves from several threads go one at a time


def save(cfg):
    """Atomic (a private temp file of its own, then a rename), one at a time. A settings file that can't be read is kept aside as
    config.json.broken first, so a load that fell back to the defaults never silently replaces the owner's settings (audit 2026-10-20)."""
    import atomic
    with _lock:
        if PATH.exists():
            try:
                json.loads(PATH.read_text())
            except ValueError:
                atomic.write_text(PATH.with_name(PATH.name + ".broken"), PATH.read_text(errors="replace"))
        atomic.write_text(PATH, json.dumps(cfg, indent=2), 0o600)
