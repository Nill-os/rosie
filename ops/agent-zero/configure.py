"""Connect a fresh Agent Zero container to Rosie (setup.sh step 7). Safe to run again.

    .venv/bin/python ops/agent-zero/configure.py MODEL

- a model preset "Local (Rosie)" on Ollama with MODEL for chat and utility (thinking off for the utility model's background
  work), selected; ctxsize.sync_agent_zero() later keeps its context window equal to Rosie's;
- the MCP server "rosie" (Rosie's tools, on the Docker bridge, with a token);
- the two prompt files Rosie writes (a0.write_include), owned by you so Rosie can update them;
- Agent Zero's API key saved in secrets/agent_zero.json for Rosie."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "app"))
import a0  # noqa: E402
import ctxsize  # noqa: E402

import a0_host  # noqa: E402

CONTAINER = __import__("os").environ.get("ROSIE_A0_CONTAINER") or a0_host.container()
PRESET = "Local (Rosie)"
INSIDE = r"""
import json, sys, yaml
sys.path.insert(0, "/a0")
want = json.loads(sys.argv[1])
d = "/a0/usr/plugins/_model_config/"
import os
os.makedirs(d, exist_ok=True)
try:
    presets = yaml.safe_load(open(d + "presets.yaml")) or []
except FileNotFoundError:
    presets = []
base = "http://host.docker.internal:11434"
kw = {"num_ctx": want["num_ctx"], **({"num_gpu": want["num_gpu"]} if want.get("num_gpu") else {})}
local = {"name": want["preset"],
         "chat": {"provider": "ollama", "name": want["model"], "api_base": base, "ctx_length": want["num_ctx"], "ctx_history": 0.7,
                  "vision": False, "max_embeds": 10, "rl_requests": 0, "rl_input": 0, "rl_output": 0, "kwargs": dict(kw)},
         "utility": {"provider": "ollama", "name": want["model"], "api_base": base, "ctx_length": want["num_ctx"], "ctx_input": 0.7,
                     "rl_requests": 0, "rl_input": 0, "rl_output": 0, "kwargs": {**kw, "reasoning_effort": "none"}},
         "embedding": {"provider": "huggingface", "name": "sentence-transformers/all-MiniLM-L6-v2", "api_base": "", "rl_requests": 0,
                       "rl_input": 0, "kwargs": {}}}
presets = [p for p in presets if p.get("name") != want["preset"]]
presets.insert(0, local)
yaml.safe_dump(presets, open(d + "presets.yaml", "w"), sort_keys=False)
cfg = {}
try:
    cfg = json.load(open(d + "config.json"))
except Exception:
    pass
cfg["model_preset"] = want["preset"]
json.dump(cfg, open(d + "config.json", "w"))
p = "/a0/usr/settings.json"
try:
    s = json.load(open(p))
except Exception:
    s = {}
servers = json.loads(s.get("mcp_servers") or '{"mcpServers": {}}')
servers.setdefault("mcpServers", {})["rosie"] = {
    "description": "Rosie's own tools: the owner's calendar, tasks, reminders, memory, mail, phone, projects, notes, PC and terminals",
    "type": "streamable-http", "url": "http://host.docker.internal:8766/mcp", "headers": {"Authorization": "Bearer " + want["token"]}}
s["mcp_servers"] = json.dumps(servers, indent=2)
json.dump(s, open(p, "w"), indent=2)
for f in ("rosie.promptinclude.md", "rosie-tools.promptinclude.md"):
    q = "/a0/usr/workdir/" + f
    os.makedirs("/a0/usr/workdir", exist_ok=True)
    open(q, "a").close()
    if want["uid"] is not None:
        os.chown(q, want["uid"], want["gid"])
from helpers.settings import get_settings
print(get_settings()["mcp_server_token"])
"""


def main():
    global CONTAINER
    model = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "qwen3.5:9b"
    import os
    if not os.environ.get("ROSIE_A0_CONTAINER"):               # this PC's container, whatever it is called (a0_host)
        found = a0_host.detect()
        if not found["found"]:
            sys.exit("Agent Zero could not be configured: " + found["why"])
        CONTAINER = found["container"]
    keys = a0.keys()                                           # makes the MCP token on first use
    want = {"preset": PRESET, "model": model, "token": keys["mcp_token"], "uid": getattr(os, "getuid", lambda: None)(), "gid": getattr(os, "getgid", lambda: None)(),
            **ctxsize.options(model)}
    want.setdefault("num_gpu", None)
    r = subprocess.run(["docker", "exec", "-i", "-w", "/a0", CONTAINER, "/opt/venv-a0/bin/python", "-", json.dumps(want)],
                       input=INSIDE, capture_output=True, text=True, timeout=120)
    if r.returncode:
        sys.exit("Agent Zero could not be configured: " + (r.stderr or r.stdout).strip()[-400:])
    key = r.stdout.strip().splitlines()[-1]
    if "--check" in sys.argv:                                  # a trial run against a spare container: save nothing, restart nothing
        print(f"   configured {CONTAINER} (check only); API key length {len(key)}")
        return
    keys["api_key"] = key
    a0.SECRETS.write_text(json.dumps(keys, indent=1))
    a0.SECRETS.chmod(0o600)
    a0.relocate()                                              # this PC's Agent Zero folder, found by a0_host
    a0.write_include()
    import config
    cfg = config.load()
    cfg["brain"] = "agent_zero"                                # owner 2026-10-23: Agent Zero is the brain behind Rosie's one chat box
    config.save(cfg)
    subprocess.run(["docker", "restart", CONTAINER], capture_output=True, timeout=120)
    print("   Agent Zero is connected to Rosie and answers her chat (preset " + PRESET + ", model " + model + ").")


if __name__ == "__main__":
    main()
