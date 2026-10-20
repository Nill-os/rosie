import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"
DEFAULTS = {
    "chat_model": "qwen3.5:9b",      # the local Ollama model Rosie thinks with (Settings > AI model); see models.py
    "owner_name": "",               # what Rosie calls you in her own prompts (her corner); empty = "your owner"
    "owner_notes": "",              # how you like help, added to her and Agent Zero's prompts (Settings > About you)
    "brain": "rosie",                # who answers chat: "rosie" = her own tool loop, "agent_zero" = Agent Zero with her tools (Settings > Brain; a0.py)
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


def save(cfg):
    import testguard
    testguard.check(PATH)
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(PATH)                                              # atomic: readers see the old file or the new one, never half
