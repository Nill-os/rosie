"""End-to-end test of Rosie as Vector's brain, out loud: the PC's speakers say each phrase, the real Vector hears it,
and the test checks what Rosie heard and did, what his face shows (his own face capture), how long answers took, and
that nothing on him crashed.

It runs this copy's app/vector_pod.py against the robot: the live Rosie's Vector server is paused for the run (Settings
> Vector off) and switched back on at the end, even when a step fails. Rosie's brain here is her own model straight
from Ollama (no tools, no chat history), so the run never writes to her chats or memory.

    ~/assistant/.venv/bin/python tools/vector_e2e.py            # all steps
    ~/assistant/.venv/bin/python tools/vector_e2e.py sit talk   # some steps

Steps: time, talk, followup, look, sit, sit_talk, stand. The timing follows him, not a clock: after "Hey... Rosie" the
question waits until he is listening, and the wake word is said again (up to WAKE_TRIES times) when he does not respond. The speaker voice is Rosie's own (af_heart): his wake word recognizes it best."""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app"))
LIVE = Path.home() / "assistant"                      # data and secrets are shared by both slots
SPEAKER_VOICE = "af_heart"                            # the "owner" talking to Vector: his wake word hears Rosie's own voice well and
                                                      # missed am_adam every time (owner 2026-10-24: "she isnt reconizing that ai voice")
ROSIE = "http://127.0.0.1:8765"
OLLAMA = "http://127.0.0.1:11434/api/chat"
SSH_OPTS = ["-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa", "-o", "ConnectTimeout=8"]

results: list[dict] = []
WAKE_TRIES = 3
WAKE = os.environ.get("E2E_WAKE", "button")             # "button" (default) or "voice" (say "Hey... Rosie" out loud)
LISTEN_WAIT_S = 4.0                                   # after the wake word: this long for his listening to start


# --- helpers ---------------------------------------------------------------------------------------------------------
def rosie_api(method, path, body=None):
    key = (LIVE / "secrets" / "session_key").read_text().strip().encode()
    cookie = hmac.new(key, b"assistant-ok", hashlib.sha256).hexdigest()
    return httpx.request(method, ROSIE + path, json=body, cookies={"assistant_session": cookie}, timeout=60).json()


_kokoro = None


def kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        vd = LIVE / "data" / "voices"
        _kokoro = Kokoro(str(vd / "kokoro-v1.0.onnx"), str(vd / "voices-v1.0.bin"))
    return _kokoro


_clips: dict = {}


def render(text: str, voice: str = SPEAKER_VOICE) -> str:
    """`text` as a wav file, made once (so it can play the moment he listens, not a second or two later)."""
    import soundfile as sf
    if (text, voice) not in _clips:
        audio, sr = kokoro().create(text, voice=voice, speed=0.95, lang="en-us")
        f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(f.name, audio, sr)
        _clips[(text, voice)] = f.name
    return _clips[(text, voice)]


def say_out_loud(text: str, voice: str = SPEAKER_VOICE):
    """The PC's speakers say `text` (default output)."""
    subprocess.run(["pw-play", render(text, voice)], check=True, timeout=60)


def robot_ssh(ip, key, cmd) -> str:
    r = subprocess.run(["ssh", "-i", str(key), "-o", f"UserKnownHostsFile={key.parent / 'known_hosts'}", *SSH_OPTS, f"root@{ip}", cmd],
                       capture_output=True, text=True, timeout=60)
    return r.stdout


def crash_dumps(ip, key) -> set:
    out = robot_ssh(ip, key, "ls /data/data/com.anki.victor/cache/crashDumps")
    return {n for n in out.split() if n.endswith(".dmp")}


def face_capture(ip, key):
    """What his screen shows now (vic-anim's own capture, 184x96)."""
    from PIL import Image
    httpx.post(f"http://{ip}:8889/consolefunccall", data={"func": "CaptureFaceImage", "args": ""}, timeout=10)
    time.sleep(0.8)
    raw = subprocess.run(["ssh", "-i", str(key), "-o", f"UserKnownHostsFile={key.parent / 'known_hosts'}", *SSH_OPTS, f"root@{ip}",
                          "cat /data/data/com.anki.victor/cache/dev_face_imgs/PROCEDURAL_ANIM_0.png"], capture_output=True, timeout=60).stdout
    import io
    return Image.open(io.BytesIO(raw)).convert("RGB")


def looks_like(capture, frame) -> bool:
    """His face shows (nearly) the screen Rosie last drew."""
    if capture is None or frame is None:
        return False
    a = np.asarray(capture, dtype=np.float32)
    b = np.asarray(frame.convert("RGB").resize(capture.size), dtype=np.float32)
    return float(np.abs(a - b).mean()) < 25.0


def wait_for(cond, secs, step=0.25):
    end = time.time() + secs
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


KEEP = Path(os.environ.get("E2E_KEEP", "")) if os.environ.get("E2E_KEEP") else None   # a folder: keep what he heard per step


def record(step, ok, **info):
    import vector_pod as vp
    info["heard_s"] = vp.status.get("heard_s")
    if KEEP is not None and vp.LAST_HEARD.exists():
        KEEP.mkdir(parents=True, exist_ok=True)
        shutil.copy(vp.LAST_HEARD, KEEP / f"{step}.wav")
    results.append({"step": step, "ok": bool(ok), **info})
    print(("PASS " if ok else "FAIL ") + step + "  " + json.dumps(info, default=str))


# --- Rosie's brain for the run (her model, no tools, nothing saved) ---------------------------------------------------
def ollama_messages(text):
    import agent
    return [{"role": "system", "content": "You are Rosie, talking out loud through Vector, a small desk robot. One to three short "
                                          "spoken sentences, no lists or emoji."},
            {"role": "user", "content": text}], agent.MODEL


def brain(text):
    import ctxsize
    msgs, model = ollama_messages(text)
    r = httpx.post(OLLAMA, json={"model": model, "stream": False, "think": False, "keep_alive": "30m", "messages": msgs,
                                 "options": {**ctxsize.options(model), "temperature": 0.6, "num_predict": 200}}, timeout=120)
    return r.json()["message"]["content"]


def brain_stream(text):
    import ctxsize
    msgs, model = ollama_messages(text)
    with httpx.stream("POST", OLLAMA, json={"model": model, "stream": True, "think": False, "keep_alive": "30m", "messages": msgs,
                                            "options": {**ctxsize.options(model), "temperature": 0.6, "num_predict": 200}}, timeout=120) as r:
        for line in r.iter_lines():
            if line.strip():
                d = json.loads(line)
                yield (d.get("message") or {}).get("content", "")
                if d.get("done"):
                    return


# --- the run ----------------------------------------------------------------------------------------------------------
def main(steps):
    import config
    import models
    import vector_pod as vp
    work = Path(tempfile.mkdtemp(prefix="vector-e2e-"))
    shutil.copytree(LIVE / "data" / "vector", work / "vector")       # same certificate, token and pinned robot as the live server
    cfg = json.loads((LIVE / "data" / "config.json").read_text())
    cfg.update(vector_on=True, vector_voice="rosie", vector_followup=True, vector_life="off",   # no lines of his own mid-test
               vector_eyes=os.environ.get("E2E_EYES", cfg.get("vector_eyes", "rosie")))
    (work / "config.json").write_text(json.dumps(cfg))
    config.PATH = work / "config.json"
    vp.DIR = work / "vector"
    for name in ("CERT", "KEY", "DOCS", "TOKEN", "SSH_KEY", "EP_CERT", "EP_KEY", "HEY_ROSIE", "LAST_HEARD"):
        setattr(vp, name, vp.DIR / getattr(vp, name).name)
    models.load_saved()
    vp.brain, vp.brain_stream = brain, brain_stream
    vp.tts = lambda text: kokoro().create(text, voice="af_heart", speed=1.0, lang="en-us")
    ip = cfg["vector_robots"][0]["ip"]
    key = vp.SSH_KEY
    before = crash_dumps(ip, key)
    print(f"Pausing the live Vector server; this copy ({REPO.name}) answers {ip} for the run.")
    rosie_api("POST", "/api/vector/settings", {"on": False})
    time.sleep(1.5)
    try:
        vp.start()
        brain("hello")                                               # model warm: the first answer is not a cold load
        last = lambda: vp.status.get("last") or {}

        def asleep_check():
            """Asleep (power save), he does not react to his wake word, and waking him (Rosie taking control, or a pick-up)
            crashed vic-engine twice on 2026-10-24 (SIGSEGV at vic-engine+0xcc4d a few seconds after he left power save).
            So the test never wakes him: it says so and waits instead."""
            bot = vp.robot()
            if bot is not None and bot.asleep():
                print("  (he is asleep: waiting up to 2 min for him to wake by himself)")
                wait_for(lambda: not bot.asleep(), 120, step=5)

        def ask(phrase, wake=True, secs=20):
            render(phrase)                                   # ready before he listens
            mark = time.time()
            if wake:
                asleep_check()                                         # his wake word needs "Hey" and "Rosie" spaced out, then a beat
                for attempt in range(1, WAKE_TRIES + 1):     # before the question (owner's note, 2026-10-21); the timing can be
                    wait_for(lambda: not vp.busy(), 20)      # off when he is slow, so the question waits for his listening
                    if WAKE == "button":                     # his wake word hears people well but not synthetic voices from
                        import vector_dev                    # the PC speakers (owner 2026-10-24): start his listening the way
                        vector_dev.listen(ip)                # his backpack button does, then ask out loud
                    else:
                        say_out_loud("Hey.")
                        time.sleep(0.35)
                        say_out_loud("Rosie.")
                    if wait_for(lambda: vp._listening.is_set(), LISTEN_WAIT_S, step=0.05):
                        break
                    print(f"  (no listening after wake word try {attempt})")
                    time.sleep(3)
                time.sleep(0.3)
            say_out_loud(phrase)
            got = wait_for(lambda: last() if last().get("at", "") >= time.strftime("%H:%M:%S", time.localtime(mark)) else None, secs)
            return got, time.time() - mark

        if "time" in steps:
            got, t = ask("what time is it?")
            record("time", got and "clock_time" in got.get("did", ""), heard=got and got.get("heard"), did=got and got.get("did"), secs=round(t, 1))
            time.sleep(4)
        if "talk" in steps or "followup" in steps:
            got, t = ask("tell me a short joke about robots.")
            spoke = wait_for(lambda: vp.busy(), 8)
            record("talk", got and got.get("did") == "Rosie answers" and spoke, heard=got and got.get("heard"), secs_to_heard=round(t, 1))
            if "followup" in steps:                          # speak the moment he listens again: his window closes after
                window = wait_for(lambda: vp._followup_until > time.time(), 60, step=0.05)   # about 3.5 s of quiet
                got2, t2 = ask("why is that funny?", wake=False, secs=20)
                record("followup", window and got2 and got2.get("heard"), window_open=bool(window), heard=got2 and got2.get("heard"))
                wait_for(lambda: not vp.busy(), 40)
                time.sleep(13)                                       # let the follow-up window close
        if "look" in steps:
            scene_t = (vp.status.get("scene") or {}).get("t", 0)
            got, t = ask("what do you see?")
            seen = wait_for(lambda: (vp.status.get("scene") or {}).get("t", 0) > scene_t, 45)
            record("look", got and seen, heard=got and got.get("heard"), saw=(vp.status.get("scene") or {}).get("saw"))
            wait_for(lambda: not vp.busy(), 40)
            time.sleep(13)                                   # let the follow-up window close
        if "sit" in steps or "sit_talk" in steps:
            got, t = ask("sit down.")      # a lone "Sit." from the speakers is too short to hear clearly
            held = wait_for(lambda: vp.sitting(), 10)
            time.sleep(8)
            frame = vp._face.last_frame if vp._face else None
            shows = looks_like(face_capture(ip, key), frame)
            record("sit", held and shows, heard=got and got.get("heard"), held=bool(held), screen_on_face=shows)
            if "sit_talk" in steps:
                got, t = ask("what time is it?")
                time.sleep(2)
                answered = got and got.get("did")
                wait_for(lambda: not vp.busy(), 30)
                back = wait_for(lambda: vp._face is not None and vp._face.last_frame is not None and looks_like(face_capture(ip, key), vp._face.last_frame), 30, step=4)
                record("sit_talk", answered and back and vp.sitting(), heard=got and got.get("heard"), did=answered, screens_back=bool(back), still_sitting=vp.sitting())
                time.sleep(13)
        if "stand" in steps:
            got, t = ask("you can move.")
            stood = wait_for(lambda: not vp.sitting(), 10)
            record("stand", got and stood, heard=got and got.get("heard"), standing=bool(stood))
    finally:
        vp.stop()
        rosie_api("POST", "/api/vector/settings", {"on": True})
        print("Live Vector server is back on.")
        new = crash_dumps(ip, key) - before
        record("no_crash", not new, new_crash_dumps=sorted(new))
        shutil.rmtree(work, ignore_errors=True)
    ok = all(r["ok"] for r in results)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} passed")
    return 0 if ok else 1


if __name__ == "__main__":
    wanted = sys.argv[1:] or ["time", "talk", "followup", "look", "sit", "sit_talk", "stand"]
    sys.exit(main(set(wanted)))
