import json
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"
DEFAULTS = {
    "chat_model": "qwen3.5:9b",
    "token_report_on": True,
    "token_goal_main_share": 50,
    "token_goal_cache_read_ratio": 80,
    "token_spike_factor": 2.0,
    "model_scout": True,
    "security_watch": True,
    "security_autopatch": True,
    "proxmox_ssh": "",
    "proxmox_ssh_key": "",
    "owner_name": "",
    "study_calendar": "Study Plan",
    "ui_theme": "aurora",
    "menu_layout": [],
    "hidden_pages": [],
    "owner_notes": "",
    "board_link_repo": "",
    "board_link_issue": 1,
    "board_link_pc": "",
    "board_link_dir": "",
    "claude_planning": False,  # board #718 safety-planning-sends-week
    "brain": "rosie",
    "a0_chat_ui": True,
    "a0_proxy_port": 8771,
    "write_target": "test",
    "test_calendar_id": None,
    "max_changes_without_confirm": 3,
    "brief_on": True,
    "brief_time": "06:30",
    "remind_events": True,
    "remind_lead_min": 15,
    "nudge_overdue": True,
    "nudge_overdue_time": "10:00",
    "scratch_mode": "nudge",
    "scratch_min_lines": 3,
    "scratch_idle_min": 30,
    "wrapup_mode": "nudge",
    "wrapup_time": "20:30",
    "gmail_accounts": [],
    "mail_on": True,
    "mail_check_min": 10,
    "mail_flag": True,
    "mail_find_tasks": True,
    "mail_digest": True,
    "canvas_time": "07:00",
    "mail_organize": False,
    "mail_organize_archive": False,
    "mail_labels": {},
    "agents": {
        "claude": "claude {prompt}",
        "codex": "codex {prompt}",
        "gemini": "gemini -i {prompt}",
        "opencode": "opencode run {prompt}",
    },
    "agents_notify": True,
    "weekly_review_on": True,
    "weekly_review_time": "17:00",
    "meeting_mic": "default",
    "meeting_call": "default",
    "meeting_keep_audio": False,
    "meeting_max_minutes": 180,
    "meeting_remind_min": 10,
    "glossary": "",
    "dictation_on": True,
    "dictation_hotkey": "Control_R",
    "dictation_min_hold_ms": 300,
    "dictation_max_seconds": 90,
    "dictation_cleanup": True,
    "dictation_source": "default",
    "dictation_notify": True,
    "sms_confirm": "ask",
    "call_confirm": "announce",
    "ai_on": True,
    "ai_hotkey": "Alt_R",
    "ai_orb_on": True,
    "ai_orb_corner": "bottom-right",
    "ai_overlay_mode": "full",
    "ai_sink": "default",
    "ai_speak": True,
    "ai_use_selection": True,
    "vector_on": False,
    "vector_host": "",
    "vector_port": 8084,
    "vector_check_port": 8085,
    "vector_personality": "",
    "vector_commands": [],
    "vector_face_panels": ["clock", "system"],
    "vector_face_seconds": 15,
    "vector_face_size": "184x96",
    "vector_quiet_idle": False,
    "vector_talk_volume": "MEDIUM",
    "vector_eyes": "rosie",
    "vector_stt_model": "base.en",
    "vector_followup": True,
    "vector_voice": "rosie",
    "vector_backpack": "own",
    "vector_life": "some",
    "vector_pad": True,
    "vector_pad_speed": "normal",
    "vector_wake_word": "hey vector",
    "vector_escapepod": False,
    "vector_robots": [],
    "taint_guard": True,  # board #718 safety-taint-guard-confirm
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
        except ValueError:  # board #718 why-guard-half-written-file
            pass
    return cfg


_lock = threading.Lock()


def save(cfg):
    # board #718 safety-keep-broken-settings
    import atomic
    with _lock:
        if PATH.exists():
            try:
                json.loads(PATH.read_text())
            except ValueError:
                atomic.write_text(PATH.with_name(PATH.name + ".broken"), PATH.read_text(errors="replace"))
        atomic.write_text(PATH, json.dumps(cfg, indent=2), 0o600)
