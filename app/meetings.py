# board #744 safety-consent-first
import datetime as dt
import difflib
import json
import os
import re
import signal
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

import config
import inbox
import pc_tools
import stt
import workflows

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "meetings"
ACTIVE = DIR / "active.json"
CONSENT_KINDS = {
    "all_parties_agreed": "Everyone on this call was told it is being recorded and agreed (recording everyone).",
    "own_voice_only": "Only my own microphone is recorded (no one else's audio).",
}
_status: dict = {}
_titles: dict = {}


def _env():
    e = dict(os.environ)
    e["PATH"] = "/usr/bin:/bin:" + e.get("PATH", "")
    e.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    e.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    return e


def node_name(target) -> str:
    out = subprocess.run(["wpctl", "inspect", target], capture_output=True, text=True, timeout=5, env=_env()).stdout
    m = re.search(r'node\.name = "([^"]+)"', out)
    d = re.search(r'node\.description = "([^"]+)"', out)
    return (m.group(1) if m else ""), (d.group(1) if d else "")


def devices() -> dict:
    cfg = config.load()
    try:
        mic = cfg["meeting_mic"] if cfg["meeting_mic"] != "default" else node_name("@DEFAULT_AUDIO_SOURCE@")[0]
        call = cfg["meeting_call"] if cfg["meeting_call"] != "default" else node_name("@DEFAULT_AUDIO_SINK@")[0]
        return {"mic": mic, "call": call, "mic_label": node_name("@DEFAULT_AUDIO_SOURCE@")[1] if cfg["meeting_mic"] == "default" else mic,
                "call_label": node_name("@DEFAULT_AUDIO_SINK@")[1] if cfg["meeting_call"] == "default" else call}
    except Exception as e:
        return {"mic": "", "call": "", "error": f"{type(e).__name__}: {e}"}


def _recorder(target, path, capture_sink):
    cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
    if target:
        cmd += ["--target", target]
    if capture_sink:
        cmd += ["-P", "stream.capture.sink=true"]
    cmd.append(str(path))
    # board #744 why-own-user-scope
    try:
        p = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", *cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_env(), start_new_session=True)
    except FileNotFoundError:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_env(), start_new_session=True)
    return p.pid


def active():
    if ACTIVE.exists():
        try:
            return json.loads(ACTIVE.read_text())
        except ValueError:
            return None
    return None


def _alive(pid):
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/cmdline").read_bytes().find(b"pw-record") >= 0
    except OSError:
        return False


def start(title, participants="", consent_kind="", consent_note="", confirmed=False, mic=None, call=None, _mic_is_sink=False):
    if active():
        raise ValueError("A recording is already running. Stop it first.")
    if consent_kind not in CONSENT_KINDS:
        raise ValueError("Choose how consent applies: everyone agreed, or only your own voice.")
    if confirmed is not True:
        raise ValueError("I won't record until you confirm consent for this meeting.")
    if consent_kind == "all_parties_agreed" and len(consent_note.strip()) < 5:
        raise ValueError("Write a few words on how everyone agreed (for example: 'announced at the start, everyone said yes').")
    cfg = config.load()
    d = devices()
    mic_t = mic or d.get("mic")
    call_t = call or d.get("call")
    if not mic_t or (consent_kind == "all_parties_agreed" and not call_t):
        raise ValueError("Could not find your audio devices: " + str(d.get("error", "no default microphone or output")))
    now = dt.datetime.now()
    mid = now.strftime("%Y%m%d-%H%M%S")
    folder = DIR / mid
    folder.mkdir(parents=True, exist_ok=True)
    pids = {"me": _recorder(mic_t, folder / "me.wav", _mic_is_sink)}
    if consent_kind == "all_parties_agreed":
        pids["them"] = _recorder(call_t, folder / "them.wav", True)
    time.sleep(0.8)
    if not all(_alive(p) for p in pids.values()):
        for p in pids.values():
            try:
                os.kill(p, signal.SIGTERM)
            except OSError:
                pass
        raise RuntimeError("The recorders did not start. Check your microphone and output devices.")
    st = {"id": mid, "title": (title or "Meeting").strip()[:80], "started": now.isoformat(timespec="seconds"), "participants": participants.strip()[:200],
          "consent": {"kind": consent_kind, "text": CONSENT_KINDS[consent_kind], "note": consent_note.strip()[:300], "confirmed_at": now.isoformat(timespec="seconds")},
          "pids": pids, "devices": {"mic": mic_t, "call": call_t if "them" in pids else ""}, "max_minutes": cfg["meeting_max_minutes"], "last_reminder": time.time()}
    ACTIVE.write_text(json.dumps(st))
    pc_tools._log("meeting_start", {"id": mid, "title": st["title"], "consent": consent_kind, "note": st["consent"]["note"]})
    inbox.notify_desktop("🔴 Recording started", f"{st['title']}: {'everyone + you' if 'them' in pids else 'your microphone only'}. Consent confirmed.")
    return public(st)


def public(st):
    started = dt.datetime.fromisoformat(st["started"])
    return {k: v for k, v in st.items() if k not in ("pids", "last_reminder")} | {"elapsed_min": round((dt.datetime.now() - started).total_seconds() / 60, 1), "tracks": list(st["pids"])}


def stop(background=True):
    st = active()
    if not st:
        raise ValueError("Nothing is being recorded.")
    for pid in st["pids"].values():
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
    time.sleep(1.2)
    ACTIVE.unlink(missing_ok=True)
    pc_tools._log("meeting_stop", {"id": st["id"]})
    inbox.notify_desktop("⏹ Recording stopped", f"{st['title']}: transcribing on this PC now.")
    _status[st["id"]] = "transcribing"
    _titles[st["id"]] = (st["title"], st["started"])
    if background:
        threading.Thread(target=process, args=(st,), daemon=True, name="meeting-transcribe").start()
    return {"id": st["id"], "status": "transcribing"}


def _load(path):
    if not Path(path).exists():
        return None
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            arr = arr.reshape(-1, w.getnchannels()).mean(axis=1)
        return arr


def merge(me, them):
    # board #744 why-drop-mic-echo-lines
    out = [{"who": "Them", **s} for s in them]
    for s in me:
        echo = any(abs(s["start"] - t["start"]) < 3 and difflib.SequenceMatcher(None, s["text"].lower(), t["text"].lower()).ratio() > 0.75 for t in them)
        if not echo:
            out.append({"who": "Me", **s})
    return sorted(out, key=lambda x: x["start"])


def process(st):
    mid, folder = st["id"], DIR / st["id"]
    try:
        extra = st.get("participants", "")
        me = stt.transcribe(_load(folder / "me.wav"), extra) if (folder / "me.wav").exists() else []
        them = stt.transcribe(_load(folder / "them.wav"), extra) if (folder / "them.wav").exists() else []
        lines = merge(me, them)
        started = dt.datetime.fromisoformat(st["started"])
        dur = max([l["end"] for l in lines] + [0])
        def stamp(sec):
            return f"{int(sec // 60):02d}:{int(sec % 60):02d}"
        body = [f"# {st['title']}", "",
                f"- **Recorded:** {started:%Y-%m-%d %H:%M} (about {max(1, round(dur / 60))} min of speech)",
                f"- **Participants:** {st['participants'] or 'not listed'}",
                f"- **Consent:** {st['consent']['text']} Note: \"{st['consent']['note'] or 'n/a'}\" (confirmed {st['consent']['confirmed_at']})",
                "- **Source:** recorded and transcribed locally by the assistant. 'Me' = your microphone, 'Them' = the call's audio. Audio " + ("kept." if config.load()["meeting_keep_audio"] else "deleted after transcription."),
                "", "## Transcript", ""]
        body += [f"**[{stamp(l['start'])}] {l['who']}:** {l['text']}" for l in lines] or ["_No speech was detected._"]
        workflows.init_vault()
        safe = re.sub(r"[^\w \-]", "", st["title"]).strip() or "Meeting"
        f = workflows.MEETINGS / f"{safe} (recording {started:%H%M}).md"
        f.write_text("\n".join(body) + "\n")
        report: list[str] = []
        try:
            workflows._sync_meetings(report, via_proposals=True)
        except Exception as e:
            report.append(f"summary step failed: {type(e).__name__}: {e}")
        st["transcript"] = ""
        for cand in workflows.MEETINGS.glob("*.md"):
            if st["consent"]["confirmed_at"] in cand.read_text(errors="replace")[:1500]:
                st["transcript"] = str(cand.relative_to(workflows.VAULT))
        if not config.load()["meeting_keep_audio"]:
            for w in ("me.wav", "them.wav"):
                (folder / w).unlink(missing_ok=True)
        (folder / "meta.json").write_text(json.dumps({k: v for k, v in st.items() if k != "pids"}, indent=1))
        _status[mid] = "done"
        inbox.add("meeting", f"Transcript ready: {st['title']}", (report[0] if report else "") + f" {len(lines)} lines.", "#/meetings", key=f"meeting:{mid}")
    except Exception as e:
        _status[mid] = f"failed: {type(e).__name__}: {e}"
        inbox.add("meeting", f"Transcript FAILED: {st['title']}", f"{type(e).__name__}: {e}. The audio was kept in data/meetings/{mid}.", "#/meetings", key=f"meetingfail:{mid}")


def recent(limit=12):
    out = []
    for p in sorted(DIR.glob("*/meta.json"), reverse=True)[:limit]:
        try:
            m = json.loads(p.read_text())
            out.append({"id": m["id"], "title": m["title"], "started": m["started"], "consent": m["consent"]["kind"], "participants": m.get("participants", ""), "status": _status.get(m["id"], "done"), "transcript": m.get("transcript", "")})
        except (ValueError, KeyError):
            continue
    for mid, s in _status.items():
        if s != "done" and not any(o["id"] == mid for o in out):
            t, started = _titles.get(mid, ("(processing)", ""))
            out.insert(0, {"id": mid, "title": t, "started": started, "consent": "", "participants": "", "status": s, "transcript": ""})
    return out


def watchdog(now=None):
    st = active()
    if not st:
        return
    if not all(_alive(p) for p in st["pids"].values()):
        stop()
        inbox.add("meeting", "A recording stopped unexpectedly", "One of the recorders ended; what was captured is being transcribed.", "#/meetings", key=f"meetingdied:{st['id']}")
        return
    started = dt.datetime.fromisoformat(st["started"])
    mins = (dt.datetime.now() - started).total_seconds() / 60
    if mins >= st["max_minutes"]:
        stop()
        inbox.add("meeting", "Recording stopped at the time limit", f"{st['max_minutes']} minutes.", "#/meetings", key=f"meetinglimit:{st['id']}")
        return
    if time.time() - st.get("last_reminder", 0) >= config.load()["meeting_remind_min"] * 60:
        st["last_reminder"] = time.time()
        ACTIVE.write_text(json.dumps(st))
        inbox.notify_desktop("🔴 Still recording", f"{st['title']} ({int(mins)} min). Open the app to stop.")
