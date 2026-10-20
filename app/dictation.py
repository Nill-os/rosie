"""System-wide push-to-talk dictation for X11. Hold the hotkey, speak, release: the text is typed into the window you were in.
Everything runs on this PC (faster-whisper, CPU). Only the hotkey is watched; other keystrokes are discarded, never stored. The text itself is never logged."""
import fcntl
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import overlay_ipc  # noqa: E402
import stt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "dictation.log"
STATUS = ROOT / "data" / "dictation.status.json"
STATUS_AI = ROOT / "data" / "ai.status.json"
PORT = int(os.environ.get("ASSISTANT_PORT", "8765"))
_ACTIVE: set = set()                    # which instance (dictate or ask) is using the microphone: only one at a time
LOCK = ROOT / "data" / "dictation.lock"
TERMINALS = {"gnome-terminal-server", "gnome-terminal", "xterm", "kitty", "alacritty", "konsole", "tilix", "terminator", "xfce4-terminal", "urxvt", "st", "wezterm", "foot", "terminology"}
FILLER = re.compile(r"(?:(?<=\s)|^)(?:u+m+|u+h+m*|e+r+m+|h+m{2,}|m+h+m+)\b[,.]?\s*", re.I)   # a bare 'mm' is left alone: it can mean millimetres


def clean(text: str) -> str:
    """Plain-rule cleanup: fillers, stutters, spacing, capitalization, a few spoken commands. Never rewrites what you said."""
    t = " " + text.strip() + " "
    t = FILLER.sub(" ", t)
    t = re.sub(r"\b(?:um+|uh+|uhm+|erm+)\b[,.]?", " ", t, flags=re.I)
    t = re.sub(r"\b(\w{1,4})(?:,? \1\b)+", lambda m: m.group(1) if m.group(1).lower() not in ("had", "that", "very", "bye", "no") else m.group(0), t, flags=re.I)
    t = re.sub(r"[,.]?\s*\bnew paragraph\b[,.]?\s*", "\n\n", t, flags=re.I)
    t = re.sub(r"[,.]?\s*\bnew line\b[,.]?\s*", "\n", t, flags=re.I)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip(" \t")
    t = re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t.strip()


def _die_with_parent():
    """If the daemon is killed or crashes, the recorder must not keep the microphone open: the kernel sends it SIGTERM."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)       # PR_SET_PDEATHSIG
    except Exception:
        pass


def kill_stale_recorders():
    """At startup: stop recorders left behind by an earlier crashed run (ours write to /tmp/dict-*.wav and have no parent)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[1])
            if "pw-record" in cmd and "/dict-" in cmd:
                parent = Path(f"/proc/{ppid}/cmdline").read_bytes().replace(b"\0", b" ").decode() if ppid > 1 else "init"
                if ppid == 1 or "systemd" in parent:           # orphaned: adopted by init or by the user's systemd, not by a running daemon
                    os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, IndexError):
            continue


class Dictation:
    def __init__(self, mode="dictate"):
        self.mode = mode                      # "dictate": type what you say.  "ask": the AI answers out loud
        self.cfg = config.load()
        self.hotkey = self.cfg["ai_hotkey"] if mode == "ask" else self.cfg["dictation_hotkey"]
        self.link = None                      # overlay link (ask mode)
        self.player = None
        self.selection = ""
        self.q: queue.Queue = queue.Queue()
        self.state = "idle"
        self.timer = None
        self.up_timer = None
        self.rec = None
        self.wav = None
        self.target = None
        self.t_press = 0.0
        self.last = {"latency": None, "words": 0}
        self.events: list = []               # recent state changes (tests and the status file)
        self.down: set = set()               # key codes currently held

    # ---------- helpers ----------
    def env(self):
        e = dict(os.environ)
        e["PATH"] = f"{Path.home()}/.local/bin:/usr/bin:/bin:" + e.get("PATH", "")
        return e

    def run(self, cmd, **kw):
        return subprocess.run(cmd, env=self.env(), capture_output=True, text=True, timeout=kw.pop("timeout", 5), **kw)

    def notify(self, title, body="", ms=1500):
        self.events.append(title)
        if self.mode == "ask":                # the octopus is the feedback; only problems also pop up
            if not (title.startswith("Dictation error") or "Didn't catch" in title or title == "Too short"):
                return
            title = {"Too short": "Too short to hear", "Dictation error": "Voice error"}.get(title, title)
        if self.cfg["dictation_notify"]:
            subprocess.Popen(["notify-send", "-a", "Dictation", "-t", str(ms), "-h", "string:x-canonical-private-synchronous:dictation", title, body], env=self.env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write_status(self, state=None, extra=None):
        try:
            (STATUS_AI if self.mode == "ask" else STATUS).write_text(json.dumps({"pid": os.getpid(), "state": state or self.state, "hotkey": self.hotkey, "updated": time.time(), **self.last, **(extra or {})}))
        except OSError:
            pass

    def log(self, **kw):
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}) + "\n")      # numbers only, never the text

    def focus(self):
        r = self.run(["xdotool", "getwindowfocus"])
        return r.stdout.strip() if r.returncode == 0 else None

    def window_class(self, wid):
        """Program name of a window. The focused window is often a child widget; the name lives on its top-level parent, so walk up."""
        if not wid:
            return ""
        try:
            from Xlib import display
            d = display.Display()
            w = d.create_resource_object("window", int(wid))
            for _ in range(12):
                cls = w.get_wm_class()
                if cls:
                    return cls[1].lower()
                parent = w.query_tree().parent
                if not parent or parent.id == d.screen().root.id:
                    break
                w = parent
        except Exception:
            pass
        r = self.run(["xdotool", "getwindowclassname", wid])
        return r.stdout.strip().lower() if r.returncode == 0 else ""

    # ---------- key state machine ----------
    def satisfied(self):
        """The hotkey is 'held' when every key of the combination is down (a single key is a combination of one)."""
        return all(self.down & group for group in self.groups)

    def handle(self, kind, keycode):
        hot = keycode in self.hot_codes
        if kind == "down":
            self.down.add(keycode)
            if hot:
                if self.up_timer:                       # a release immediately followed by a press is key auto-repeat: ignore it
                    self.up_timer.cancel()
                    self.up_timer = None
                    return
                if self.state in ("idle", "speaking") and self.satisfied():
                    self._begin()
            elif self.state == "hold":                  # another key while holding = a shortcut (Ctrl+C...), not dictation
                self.timer and self.timer.cancel()
                self._stop_recorder(discard=True)
                self.state = "cancelled"
                _ACTIVE.discard(self.mode)
        else:
            self.down.discard(keycode)
            if hot and not self.satisfied() and self.state in ("hold", "rec", "cancelled"):
                if self.up_timer:
                    self.up_timer.cancel()
                self.up_timer = threading.Timer(0.04, self._hot_up)
                self.up_timer.start()

    def _begin(self):
        """Key (or orb) pressed: start recording. A quick tap is discarded, so shortcuts and accidents do nothing."""
        if self.state == "speaking":                    # pressing while it talks interrupts it (barge-in)
            self.stop_playback()
            self.state = "idle"
            self.link and self.link.state("idle", "")
        if _ACTIVE - {self.mode}:                       # the other mode is using the microphone right now
            return
        _ACTIVE.add(self.mode)
        self.state = "hold"
        self.t_press = time.time()
        self.target = self.focus()
        self.selection = ""
        if self.mode == "ask" and self.cfg.get("ai_use_selection", True):
            r = self.run(["xclip", "-selection", "primary", "-o"])
            self.selection = r.stdout[:1500] if r.returncode == 0 else ""
        self._start_recorder()
        self.timer = threading.Timer(self.cfg["dictation_min_hold_ms"] / 1000, self._commit)
        self.timer.start()

    def orb_event(self, kind):
        """The on-screen orb: press and hold = hold the key."""
        if kind == "down" and self.state in ("idle", "speaking"):
            self._begin()
        elif kind == "up":
            self._hot_up()

    def stop_playback(self):
        p, self.player = self.player, None
        if p and p.poll() is None:
            p.terminate()

    def _hot_up(self):
        self.up_timer = None
        if self.state == "hold":                        # a quick tap: not dictation
            self.timer and self.timer.cancel()
            self._stop_recorder(discard=True)
            self.state = "idle"
            _ACTIVE.discard(self.mode)
        elif self.state == "rec":
            self._finish()
        elif self.state == "cancelled":
            self.state = "idle"
            _ACTIVE.discard(self.mode)

    def _commit(self):
        if self.state == "hold":
            self.state = "rec"
            self.notify("🎤 Listening…", "Release the key when you are done", 60000)
            self.write_status()
            if self.link and self.mode == "ask":
                self.link.state("listening", "")
                threading.Thread(target=self._meter, daemon=True).start()
            self.max_timer = threading.Timer(self.cfg["dictation_max_seconds"], lambda: self.state == "rec" and self._finish())
            self.max_timer.start()

    # ---------- recording ----------
    def _start_recorder(self):
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="dict-")
        os.close(fd)
        self.wav = path
        cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
        src = self.cfg["dictation_source"]
        if src != "default":
            cmd += ["--target", src]
            if self.cfg.get("_source_is_sink"):
                cmd += ["-P", "stream.capture.sink=true"]
        self.rec = subprocess.Popen(cmd + [path], env=self.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=_die_with_parent)

    def _stop_recorder(self, discard=False):
        if self.rec:
            try:
                self.rec.send_signal(signal.SIGINT)
                self.rec.wait(timeout=3)
            except Exception:
                self.rec.kill()
            self.rec = None
        if discard and self.wav:
            try:
                os.unlink(self.wav)
            except OSError:
                pass
            self.wav = None

    def _finish(self):
        if getattr(self, "max_timer", None):
            self.max_timer.cancel()
        self.state = "processing"
        t_release = time.time()
        self._stop_recorder()
        wav, target = self.wav, self.target
        self.wav = None
        threading.Thread(target=self._process_ask if self.mode == "ask" else self._process, args=(wav, target, t_release), daemon=True).start()

    def _process(self, wav, target, t_release):
        try:
            with wave.open(wav, "rb") as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            os.unlink(wav)
            if len(pcm) < 16000 * 0.4:
                self.notify("Too short", "", 800)
                return
            segs = stt.transcribe(pcm, model=self.cfg.get("dictation_model", "small.en"))
            text = " ".join(s["text"] for s in segs).strip()
            if not text:
                self.notify("Didn't catch that", "", 1000)
                return
            if self.cfg["dictation_cleanup"]:
                text = clean(text)
            where = self.inject(text, target)
            self.last = {"latency": round(time.time() - t_release, 2), "words": len(text.split())}
            self.log(words=len(text.split()), audio_s=round(len(pcm) / 16000, 1), latency_s=self.last["latency"], where=where)
            self.notify("✔ " + ("typed" if where == "typed" else "copied: paste with Ctrl+V"), f"{self.last['words']} words", 1200)
        except Exception as e:
            self.notify("Dictation error", f"{type(e).__name__}: {e}", 4000)
            self.log(error=f"{type(e).__name__}: {e}")
        finally:
            self.state = "idle"
            _ACTIVE.discard(self.mode)
            self.write_status()

    # ---------- asking the AI ----------
    def _meter(self):
        """While recording: tell the overlay how loud you are (reads the end of the growing wav file)."""
        while self.state == "rec" and self.wav:
            try:
                with open(self.wav, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(44, size - 3200))
                    pcm = np.frombuffer(f.read()[: (size - max(44, size - 3200)) // 2 * 2], dtype=np.int16)
                if len(pcm):
                    self.link.level(min(1.0, float(np.sqrt(np.mean((pcm / 32768.0) ** 2))) * 9))
            except OSError:
                pass
            time.sleep(0.08)

    def _api(self, path, body):
        import hashlib
        import hmac
        key = (ROOT / "secrets" / "session_key").read_text().strip().encode()
        cookie = hmac.new(key, b"assistant-ok", hashlib.sha256).hexdigest()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Cookie": f"assistant_session={cookie}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()

    @staticmethod
    def spoken(text):
        """Plain words for the voice: no markdown or links, and not a whole essay."""
        t = re.sub(r"https?://\S+", "a link", text)
        t = re.sub(r"[*_`#>|]+", "", t)
        t = re.sub(r"\n\s*[-•]\s*", ". ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) > 700:
            cut = t[:700]
            t = cut[: max(cut.rfind(". "), 300) + 1] + " The rest is in your voice chat."
        return t

    def _process_ask(self, wav, target, t_release):
        try:
            with wave.open(wav, "rb") as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            os.unlink(wav)
            if len(pcm) < 16000 * 0.4:
                self.notify("Too short", "", 800)
                return
            self.link and self.link.state("thinking", "")
            segs = stt.transcribe(pcm, model=self.cfg.get("dictation_model", "small.en"))
            text = " ".join(s["text"] for s in segs).strip()
            if not text:
                self.notify("Didn't catch that", "", 1000)
                return
            self.link and self.link.state("thinking", text[:80])
            res = json.loads(self._api("/api/voice", {"text": text, "selection": self.selection}))
            reply = res.get("reply") or "I have nothing to say to that."
            self.last = {"latency": round(time.time() - t_release, 2), "words": len(text.split())}
            self.log(words=len(text.split()), audio_s=round(len(pcm) / 16000, 1), latency_s=self.last["latency"], where="ask")
            self.events.append("answered")
            if self.cfg.get("ai_speak", True):
                self._say(self.spoken(reply))
            else:
                self.link and self.link.state("speaking", reply[:90])
                time.sleep(min(6, 1 + len(reply) / 40))
        except Exception as e:
            self.notify("Dictation error", f"{type(e).__name__}: {e}", 4000)
            self.log(error=f"{type(e).__name__}: {e}")
        finally:
            if self.state in ("processing", "speaking"):        # unless you already pressed again (barge-in)
                self.state = "idle"
                _ACTIVE.discard(self.mode)
                self.link and self.link.state("idle", "")
            self.write_status()

    def _say(self, text):
        import io
        import soundfile as sf
        self.state = "speaking"
        raw = self._api("/api/speak", {"text": text})
        if self.state != "speaking":                    # you interrupted while the voice was still being made
            return
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="ai-say-")
        os.close(fd)
        sf.write(path, audio, sr)
        hop = int(sr * 0.08)
        env = [float(np.sqrt(np.mean(audio[i:i + hop] ** 2))) for i in range(0, len(audio), hop)]
        peak = max(env) or 1.0
        self.link and self.link.state("speaking", text[:90])
        self.player = subprocess.Popen(["pw-play", *(["--target", self.cfg["ai_sink"]] if self.cfg.get("ai_sink", "default") != "default" else []), path], env=self.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p, t0 = self.player, time.time()
        while p.poll() is None and self.state == "speaking":
            i = int((time.time() - t0) / 0.08)
            self.link and self.link.level(env[i] / peak if i < len(env) else 0.0)
            time.sleep(0.08)
        self.stop_playback()
        try:
            os.unlink(path)
        except OSError:
            pass

    # ---------- typing it in ----------
    def clipboard_get(self):
        r = self.run(["xclip", "-selection", "clipboard", "-o"])
        return r.stdout if r.returncode == 0 else None

    def clipboard_set(self, text):
        p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, env=self.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.stdin.write(text.encode())
        p.stdin.close()                                  # xclip stays alive serving the selection until replaced
        time.sleep(0.08)

    def inject(self, text, target):
        if target and self.focus() != target:            # you moved to another window while it was transcribing: never type into the wrong place
            self.clipboard_set(text)
            return "clipboard"
        old = self.clipboard_get()
        self.clipboard_set(text)
        chord = "ctrl+shift+v" if self.window_class(target) in TERMINALS else "ctrl+v"
        self.run(["xdotool", "key", "--clearmodifiers", chord])
        time.sleep(0.25)
        if old is not None:
            self.clipboard_set(old)                      # put back what you had copied
        return "typed"

    # ---------- main loop ----------
    def start(self):
        from Xlib import X, XK, display
        from Xlib.ext import record
        from Xlib.protocol import rq
        d = display.Display()
        mods = d.get_modifier_mapping()
        groups = []
        for token in self.hotkey.split("+"):         # e.g. "Control_R" or the Copilot key: "Shift_L+Super_L+F23"
            token = token.strip()
            if token.lower().startswith("keycode:"):          # a raw key number, for keys with no standard name (the Copilot key is keycode:201 on many layouts)
                try:
                    kc = int(token.split(":", 1)[1])
                except ValueError:
                    kc = 0
                if not 8 <= kc <= 255:
                    raise SystemExit(f"Unknown hotkey '{self.hotkey}': key numbers are 8 to 255.")
            else:
                sym = XK.string_to_keysym(token)
                if not sym:
                    raise SystemExit(f"Unknown hotkey '{self.hotkey}'. Use X key names such as Control_R, Pause, F9, or a combination like Shift_L+Super_L+keycode:201.")
                kc = d.keysym_to_keycode(sym)
                if not kc:
                    raise SystemExit(f"This keyboard layout has no key named '{token}'. Use the key number instead, like keycode:201.")
            codes = {kc}
            for row in mods:                              # left and right Ctrl/Shift/Alt/Super count as the same key
                if kc in row:
                    codes |= {c for c in row if c}
            groups.append(codes)
        self.groups = groups
        self.hot_codes = set().union(*groups)
        ctx_d = display.Display()
        ctx = ctx_d.record_create_context(0, [record.AllClients], [{"core_requests": (0, 0), "core_replies": (0, 0), "ext_requests": (0, 0, 0, 0), "ext_replies": (0, 0, 0, 0),
                                                                    "delivered_events": (0, 0), "device_events": (X.KeyPress, X.KeyRelease), "errors": (0, 0), "client_started": False, "client_died": False}])

        def cb(reply):
            if reply.category != record.FromServer or reply.client_swapped or not reply.data or reply.data[0] < 2:
                return
            data = reply.data
            while len(data):
                ev, data = rq.EventField(None).parse_binary_value(data, ctx_d.display, None, None)
                if ev.type in (X.KeyPress, X.KeyRelease):
                    self.q.put(("down" if ev.type == X.KeyPress else "up", ev.detail))        # only the key code: nothing else is kept
        self.ctx, self.ctx_d = ctx, ctx_d
        threading.Thread(target=lambda: ctx_d.record_enable_context(ctx, cb), daemon=True).start()
        kill_stale_recorders()
        threading.Thread(target=lambda: stt.get_model(self.cfg.get("dictation_model", "small.en")), daemon=True).start()     # warm the model
        self.write_status("ready")

    def loop(self):
        while True:
            try:
                kind, code = self.q.get(timeout=1)
            except queue.Empty:
                self.write_status()
                continue
            try:
                self.handle(kind, code)
            except Exception as e:
                self.log(error=f"{type(e).__name__}: {e}")

    def stop(self):
        try:
            self.ctx_d.record_disable_context(self.ctx)
            self.ctx_d.flush()
        except Exception:
            pass


def main():
    LOCK.parent.mkdir(exist_ok=True)
    lf = open(LOCK, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Dictation is already running.")
    cfg = config.load()
    if not (cfg["dictation_on"] or cfg["ai_on"]):
        raise SystemExit("Dictation and the AI hold-to-talk are both switched off in Settings.")
    modes = []
    if cfg["dictation_on"]:
        modes.append(Dictation("dictate"))
    if cfg["ai_on"]:
        ask = Dictation("ask")
        if modes and set(ask.hotkey.split("+")) & set(modes[0].hotkey.split("+")):
            print(f"AI key '{ask.hotkey}' overlaps the dictation key '{modes[0].hotkey}': the AI key is switched off. Choose different keys in Settings.", flush=True)
            ask.link = None
            ask = None
        if ask:
            ask.link = overlay_ipc.OverlayLink(on_orb=ask.orb_event)
            modes.append(ask)
    signal.signal(signal.SIGTERM, lambda *a: ([m.stop() for m in modes], sys.exit(0)))
    for m in modes:
        m.start()
        print(f"{'Dictation' if m.mode == 'dictate' else 'AI hold-to-talk'} ready: hold {m.hotkey}" + (", release to type." if m.mode == "dictate" else ", ask, release to hear the answer (or press and hold the orb)."), flush=True)
    for m in modes[1:]:
        threading.Thread(target=m.loop, daemon=True).start()
    modes[0].loop()


if __name__ == "__main__":
    main()
