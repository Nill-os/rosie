# board #788 owner-vector-runs-rosie
import re
import subprocess

from fastapi import APIRouter

import config
import vector_bot
import vector_face
import vector_intents
import vector_life
import vector_pad
import vector_pod

router = APIRouter()
IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _ok(fn, *a):
    try:
        return {"ok": True, "message": fn(*a)}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": (e.stderr or e.stdout or str(e)).strip()[-400:]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/api/vector")
def vector_status():
    cfg = config.load()
    robots = cfg.get("vector_robots") or []
    bot = robots[0] if robots else None
    return {"on": bool(cfg.get("vector_on")), "running": vector_pod.status["running"], "host": vector_pod.host(),
            "port": vector_pod.EP_PORT if cfg.get("vector_escapepod") else cfg.get("vector_port"),
            "escapepod": bool(cfg.get("vector_escapepod")), "port_rule": "" if vector_pod.port_open_to_users(vector_pod.EP_PORT) else vector_pod.PORT_RULE, "check_port": cfg.get("vector_check_port"), "robots": robots,
            "online": bool(bot and bot.get("ip") and vector_bot.reachable(bot["ip"])),
            "held": bool(vector_pod._robot_obj and vector_pod._robot_obj.held),
            "last": vector_pod.status["last"], "history": vector_pod.status.get("history", [])[-20:], "error": vector_pod.status["error"],
            "personality": cfg.get("vector_personality", ""), "commands": cfg.get("vector_commands") or [],
            "built_in": vector_intents.DEFAULT_COMMANDS, "face_panels": cfg.get("vector_face_panels") or [], "face_seconds": cfg.get("vector_face_seconds") or 15,
            "quiet_idle": bool(cfg.get("vector_quiet_idle")), "wake_word": cfg.get("vector_wake_word") or "hey vector", "backpack": cfg.get("vector_backpack") or "own", "voice": cfg.get("vector_voice") or "rosie", "eyes": cfg.get("vector_eyes") or "rosie",
            "followup": cfg.get("vector_followup", True),
            "life": {"level": cfg.get("vector_life") or "some", "mood": vector_pod._alive.mood.describe() if vector_pod._alive else "", "last": vector_pod._alive.last if vector_pod._alive else None}, "stt_model": cfg.get("vector_stt_model") or "base.en", "talk_volume": cfg.get("vector_talk_volume") or "MEDIUM",
            "pad": {**vector_pad.status, "on": cfg.get("vector_pad", True), "speed": cfg.get("vector_pad_speed") or "normal"}, "wirepod": (vector_pod.WIREPOD / "jdocs" / "jdocs.json").exists()}


@router.post("/api/vector/settings")
def vector_settings(body: dict):
    cfg = config.load()
    if "personality" in body:
        cfg["vector_personality"] = str(body["personality"])[:2000]
    if "host" in body:
        cfg["vector_host"] = str(body["host"]).strip()[:100]
    if "commands" in body:
        cmds = []
        for c in body["commands"] or []:
            phrases = [str(p).strip() for p in c.get("phrases", []) if str(p).strip()][:20]
            if phrases and c.get("action") in ("hold", "release", "say"):
                cmds.append({"phrases": phrases, "action": c["action"], "text": str(c.get("text", ""))[:500], "reply": str(c.get("reply", ""))[:300]})
        cfg["vector_commands"] = cmds
    if body.get("talk_volume") in ("LOW", "MEDIUM_LOW", "MEDIUM", "MEDIUM_HIGH", "HIGH"):
        cfg["vector_talk_volume"] = body["talk_volume"]
    if body.get("eyes") in ("rosie", "own"):
        cfg["vector_eyes"] = body["eyes"]
    if "followup" in body:
        cfg["vector_followup"] = bool(body["followup"])
    if body.get("life") in vector_life.LEVELS:
        cfg["vector_life"] = body["life"]
    if "pad" in body:
        cfg["vector_pad"] = bool(body["pad"])
    if body.get("pad_speed") in vector_pad.SPEEDS:
        cfg["vector_pad_speed"] = body["pad_speed"]
    if body.get("stt_model") in ("base.en", "small.en"):
        cfg["vector_stt_model"] = body["stt_model"]
    if body.get("voice") in ("rosie", "vector"):
        cfg["vector_voice"] = body["voice"]
    if "quiet_idle" in body:
        cfg["vector_quiet_idle"] = bool(body["quiet_idle"])
    if "face_seconds" in body:
        try:
            cfg["vector_face_seconds"] = max(5, min(120, int(body["face_seconds"])))
        except (TypeError, ValueError):
            pass
    if "face_panels" in body:
        cfg["vector_face_panels"] = [p for p in body["face_panels"] or [] if p in vector_face.PANELS]
    if "on" in body:
        cfg["vector_on"] = bool(body["on"])
    config.save(cfg)
    if "quiet_idle" in body or "talk_volume" in body:  # board #788 why-quiet-idle-volume
        bot = vector_pod.robot() if cfg.get("vector_robots") else None
        if bot is not None:
            try:
                bot.set_volume("MUTE" if cfg["vector_quiet_idle"] else cfg.get("vector_talk_volume") or "MEDIUM")
            except Exception as e:
                return {"ok": False, "error": f"Saved, but Vector did not answer: {type(e).__name__}"}
    if "on" in body:
        if cfg["vector_on"]:
            return _ok(lambda: vector_pod.start() and "Rosie's Vector server is on.")
        vector_pod.stop()
    return {"ok": True}


@router.post("/api/vector/import")
def vector_import():
    return _ok(lambda: f"Took over {vector_pod.import_wirepod()} settings documents from wire-pod.")


@router.post("/api/vector/setup")
def vector_setup(body: dict):
    ip = str(body.get("ip", "")).strip()
    if not IP.match(ip):
        return {"ok": False, "error": "Type Vector's address, like 192.168.1.15."}
    if not config.load().get("vector_on"):
        return {"ok": False, "error": "Turn the Vector server on first."}
    return _ok(vector_pod.connect, ip)


@router.post("/api/vector/wake")
def vector_wake(body: dict):
    robots = config.load().get("vector_robots") or []
    if not robots:
        return {"ok": False, "error": "Connect your robot first."}
    if not vector_bot.reachable(robots[0]["ip"], 22):
        return {"ok": False, "error": "His wake word can only change on WireOS (his firmware has no SSH)."}
    return _ok(vector_pod.set_wake_word, robots[0]["ip"], bool(body.get("rosie", True)))


@router.post("/api/vector/backpack")
def vector_backpack(body: dict):
    robots = config.load().get("vector_robots") or []
    if not robots:
        return {"ok": False, "error": "Connect your robot first."}
    if not vector_bot.reachable(robots[0]["ip"], 22):
        return {"ok": False, "error": "His back lights can only change on WireOS (his firmware has no SSH)."}
    return _ok(vector_pod.set_backpack_theme, robots[0]["ip"], bool(body.get("rosie", True)))


@router.post("/api/vector/undo")
def vector_undo(body: dict):
    ip = str(body.get("ip", "")).strip()
    if not IP.match(ip):
        return {"ok": False, "error": "Type Vector's address, like 192.168.1.15."}
    return _ok(vector_pod.undo_robot, ip)


@router.post("/api/vector/{what}")
def vector_act(what: str, body: dict | None = None):
    bot = vector_pod.robot

    def hold():
        if not vector_pod.sit(bot()):
            raise TimeoutError("Vector did not hand over control.")
        return "He stays where he is, with his screens on."
    acts = {"hold": hold,
            "release": lambda: (vector_pod.stand(bot()), "He can move again.")[1],
            "say": lambda: (vector_pod.speak(str((body or {}).get("text", ""))[:600]), "Said.")[1]}
    if what not in acts:
        return {"ok": False, "error": "Unknown action."}
    if vector_pod.robot() is None:
        return {"ok": False, "error": "Set up your robot first."}
    return _ok(acts[what])
